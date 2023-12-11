from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from starlette.responses import HTMLResponse
from toolz import assoc

from helioweb.infra.config import HTTPS_URLS
from helioweb.infra.core import get_mongodb
from helioweb.ui.util import raise404_if_none

app = FastAPI(docs_url="/apidocs")
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).parent.joinpath("static")),
    name="static",
)
templates = Jinja2Templates(directory=Path(__file__).parent.joinpath("templates"))
templates.env.globals.update({"GLOBALS_today_year": str(date.today().year)})


def oax_api_link_for(id_: str):
    return id_.replace("https://openalex.org", "https://api.openalex.org")


def https_url_for(request: Request, name: str, **path_params: Any) -> str:
    http_url = request.url_for(name, **path_params)
    # Replace 'http' with 'https'
    return http_url.replace(scheme="https") if HTTPS_URLS else http_url


templates.env.globals["https_url_for"] = https_url_for


@app.get("/", response_class=HTMLResponse)
async def read_home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})


@app.get("/docs", response_class=HTMLResponse)
async def read_docs(request: Request):
    return templates.TemplateResponse("docs.html", {"request": request})


@app.get("/author:{orcid:path}", response_class=HTMLResponse)
async def author_home(request: Request, orcid: str, mdb=Depends(get_mongodb)):
    author = raise404_if_none(mdb.alldocs.find_one({"_id": orcid}))
    author_concepts = list(
        mdb.alldocs.find(
            {
                "type": "Concept",
                "_id": {"$in": [edge["o"] for edge in author["outgoing"]]},
            }
        )
    )
    author_concepts = sorted(
        author_concepts,
        key=lambda concept: (
            next(edge for edge in author["outgoing"] if edge["o"] == concept["_id"])[
                "q"
            ],
            concept["display_name"],
        ),
        reverse=True,
    )
    author_works = list(mdb.alldocs.find({"type": "Work", "outgoing.o": author["_id"]}))
    author_works = sorted(
        author_works,
        key=lambda work: (work["ads_work"]["year"], work["display_name"]),
        reverse=True,
    )
    author_coauthors = mdb.alldocs.aggregate(
        [
            {"$match": {"type": "Work", "outgoing.o": author["_id"]}},
            {"$project": {"outgoing": 1}},
            {"$unwind": {"path": "$outgoing"}},
            {"$match": {"outgoing.p": "author"}},
            {"$project": {"_id": "$outgoing.o"}},
            {"$group": {"_id": "$_id"}},
            {
                "$lookup": {
                    "from": "alldocs",
                    "localField": "_id",
                    "foreignField": "_id",
                    "as": "author",
                }
            },
            {"$match": {"author._id": {"$exists": True, "$ne": author["_id"]}}},
            {"$project": {"display_name": {"$first": "$author.display_name"}}},
            {"$sort": {"display_name": 1}},
        ],
        allowDiskUse=True,
    )
    author_collaborating_institutions = mdb.alldocs.aggregate(
        [
            {"$match": {"type": "Work", "outgoing.o": author["_id"]}},
            {"$project": {"outgoing": 1}},
            {"$unwind": {"path": "$outgoing"}},
            {"$match": {"outgoing.p": "affil"}},
            {"$project": {"_id": "$outgoing.o"}},
            {"$group": {"_id": "$_id"}},
            {
                "$lookup": {
                    "from": "alldocs",
                    "localField": "_id",
                    "foreignField": "_id",
                    "as": "institution",
                }
            },
            {"$match": {"institution._id": {"$exists": True}}},
            {"$project": {"display_name": {"$first": "$institution.display_name"}}},
            {"$sort": {"display_name": 1}},
        ],
        allowDiskUse=True,
    )
    author_oax_api_link = oax_api_link_for(author.get("oax_author", {}).get("id", ""))
    return templates.TemplateResponse(
        "author.html",
        {
            "request": request,
            "author": author,
            "author_coauthors": author_coauthors,
            "author_collaborating_institutions": author_collaborating_institutions,
            "author_oax_api_link": author_oax_api_link,
            "author_concepts": author_concepts,
            "author_works": author_works,
        },
    )


@app.get("/work:{work_id:path}", response_class=HTMLResponse)
async def work_home(request: Request, work_id: str, mdb=Depends(get_mongodb)):
    work = raise404_if_none(mdb.alldocs.find_one({"_id": work_id}))
    work_authors = sorted(
        list(
            assoc(
                doc, "q", next(w["q"] for w in work["outgoing"] if w["o"] == doc["_id"])
            )
            for doc in mdb.alldocs.find(
                {
                    "type": "Author",
                    "_id": {
                        "$in": [w["o"] for w in work["outgoing"] if w["p"] == "author"]
                    },
                }
            )
        ),
        key=lambda author: author["display_name"],
    )
    work_affils = sorted(
        list(
            mdb.alldocs.find(
                {
                    "type": "Institution",
                    "_id": {
                        "$in": [w["o"] for w in work["outgoing"] if w["p"] == "affil"]
                    },
                }
            )
        ),
        key=lambda affil: affil["display_name"],
    )
    return templates.TemplateResponse(
        "work.html",
        {
            "request": request,
            "work": work,
            "work_authors": work_authors,
            "work_affils": work_affils,
        },
    )


@app.get("/affil:{affil_id:path}", response_class=HTMLResponse)
async def affil_home(request: Request, affil_id: str, mdb=Depends(get_mongodb)):
    affil = raise404_if_none(mdb.alldocs.find_one({"_id": affil_id}))
    if affil.get("outgoing"):
        affil_parents = sorted(
            list(
                mdb.alldocs.find(
                    {
                        "type": "Institution",
                        "_id": {
                            "$in": [
                                a["o"]
                                for a in affil["outgoing"]
                                if a["p"] == "skos:broader"
                            ]
                        },
                    }
                )
            ),
            key=lambda affil: affil["display_name"],
        )
    else:
        affil_parents = []
    affil_children = sorted(
        list(
            mdb.alldocs.find(
                {
                    "type": "Institution",
                    "outgoing": {
                        "$elemMatch": {"p": "skos:broader", "o": affil["_id"]}
                    },
                }
            )
        ),
        key=lambda affil: affil["display_name"],
    )
    affil_works = sorted(
        list(mdb.alldocs.find({"type": "Work", "outgoing.o": affil["_id"]})),
        key=lambda work: (work["ads_work"]["year"], work["display_name"] or ""),
    )
    affil_collaborating_authors = mdb.alldocs.aggregate(
        [
            {"$match": {"type": "Work", "outgoing.o": affil["_id"]}},
            {"$project": {"outgoing": 1}},
            {"$unwind": {"path": "$outgoing"}},
            {"$match": {"outgoing.p": "author"}},
            {"$project": {"_id": "$outgoing.o"}},
            {"$group": {"_id": "$_id"}},
            {
                "$lookup": {
                    "from": "alldocs",
                    "localField": "_id",
                    "foreignField": "_id",
                    "as": "author",
                }
            },
            {"$match": {"author._id": {"$exists": True}}},
            {"$project": {"display_name": {"$first": "$author.display_name"}}},
            {"$sort": {"display_name": 1}},
        ],
        allowDiskUse=True,
    )
    affil_ads_id = affil["_id"].split("/")[-1]
    return templates.TemplateResponse(
        "affil.html",
        {
            "request": request,
            "affil": affil,
            "affil_collaborating_authors": affil_collaborating_authors,
            "affil_ads_id": affil_ads_id,
            "affil_parents": affil_parents,
            "affil_children": affil_children,
            "affil_works": affil_works,
        },
    )


@app.get("/concept:{concept_id:path}", response_class=HTMLResponse)
async def concept_home(request: Request, concept_id: str, mdb=Depends(get_mongodb)):
    concept = raise404_if_none(mdb.alldocs.find_one({"_id": concept_id}))
    if concept.get("outgoing"):
        concept_parents = sorted(
            list(
                mdb.alldocs.find(
                    {
                        "type": "Concept",
                        "_id": {
                            "$in": [
                                c["o"]
                                for c in concept["outgoing"]
                                if c["p"] == "skos:broader"
                            ]
                        },
                    }
                )
            ),
            key=lambda concept: concept["display_name"],
        )
    else:
        concept_parents = []
    concept_children = sorted(
        list(
            mdb.alldocs.find(
                {
                    "type": "Concept",
                    "outgoing": {
                        "$elemMatch": {"p": "skos:broader", "o": concept["_id"]}
                    },
                }
            )
        ),
        key=lambda concept: concept["display_name"],
    )
    concept_authors = sorted(
        list(
            mdb.alldocs.find(
                {
                    "type": "Author",
                    "outgoing": {
                        "$elemMatch": {"p": "dcterms:relation", "o": concept_id}
                    },
                }
            )
        ),
        key=lambda author: author["display_name"],
    )
    concept_oax_api_link = oax_api_link_for(concept["_id"])
    return templates.TemplateResponse(
        "concept.html",
        {
            "request": request,
            "concept": concept,
            "concept_oax_api_link": concept_oax_api_link,
            "concept_parents": concept_parents,
            "concept_children": concept_children,
            "concept_authors": concept_authors,
        },
    )

from datetime import date
from gettext import gettext, ngettext, pgettext, npgettext
from pathlib import Path
from typing import Any, Annotated

from fastapi import FastAPI, Depends, Query
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from starlette.responses import HTMLResponse
from toolz import assoc, merge, concatv

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
templates.env.add_extension("jinja2.ext.i18n")
templates.env.install_gettext_callables(gettext, ngettext)


def oax_api_link_for(id_: str):
    return id_.replace("https://openalex.org", "https://api.openalex.org")


def https_url_for(request: Request, name: str, **path_params: Any):
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


@app.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    q: str = "",
    t: str | None = None,
    mdb=Depends(get_mongodb),
):
    filter_ = {"$text": {"$search": q}}
    if t is not None:
        filter_ = merge(filter_, {"type": t})
    results = list(
        mdb.alldocs.find(
            filter=filter_,
            projection={"score": {"$meta": "textScore"}},
            sort={"score": {"$meta": "textScore"}},
            limit=50,
        )
    )
    for r in results:
        match r["type"]:
            case "Author":
                r["_href"] = "author:" + r["_id"]
            case "Concept":
                r["_href"] = "concept:" + r["_id"]
            case "Institution":
                r["_href"] = "affil:" + r["_id"]
            case "Work":
                r["_href"] = "work:" + r["_id"]
            case _:
                r["_href"] = "_:" + r["_id"]

    return templates.TemplateResponse(
        "search.html", {"request": request, "q": q, "results": results, "t": t}
    )


@app.get("/funnel_authors", response_class=HTMLResponse)
async def funnel_authors(
    request: Request,
    concept: Annotated[list[str] | None, Query()] = None,
    institution: Annotated[list[str] | None, Query()] = None,
    coauthor: Annotated[list[str] | None, Query()] = None,
    mdb=Depends(get_mongodb),
):
    qarg_values = {
        "concept": concept or ["", "", ""],
        "institution": institution or ["", "", ""],
        "coauthor": coauthor or ["", "", ""],
    }
    for qarg, _ in qarg_values.items():
        for len_ in (1, 2):
            if len(qarg_values[qarg]) == len_:
                qarg_values[qarg].append("")

    if (
        any(qarg_values["concept"])
        or any(qarg_values["institution"])
        or any(qarg_values["coauthor"])
    ):
        authors_with_concepts_filter = {"type": "Author"}
        if any(qarg_values["concept"]):
            authors_with_concepts_filter["outgoing"] = {
                "$all": [
                    {"$elemMatch": {"p": "dcterms:relation", "o": c}}
                    for c in filter(None, qarg_values["concept"])
                ]
            }

        author_ids_from_concepts = [
            d["_id"] for d in mdb.alldocs.find(authors_with_concepts_filter)
        ]

        works_with_authors_filter = {"type": "Work", "outgoing.p": "author"}
        if any(qarg_values["institution"]) or any(qarg_values["coauthor"]):
            works_with_authors_filter["outgoing"] = {"$all": []}
        if any(qarg_values["institution"]):
            works_with_authors_filter["outgoing"]["$all"].extend(
                [
                    {"$elemMatch": {"p": "affil", "o": i}}
                    for i in filter(None, qarg_values["institution"])
                ]
            )
        if any(qarg_values["coauthor"]):
            works_with_authors_filter["outgoing"]["$all"].extend(
                [
                    {"$elemMatch": {"p": "author", "o": a}}
                    for a in filter(None, qarg_values["coauthor"])
                ]
            )
        author_ids_from_institutions_and_coauthors = [
            d["_id"]
            for d in mdb.alldocs.aggregate(
                [
                    {"$match": works_with_authors_filter},
                    {"$project": {"_id": 0, "outgoing": 1}},
                    {"$unwind": {"path": "$outgoing"}},
                    {"$match": {"outgoing.p": "author"}},
                    {"$project": {"_id": "$outgoing.o"}},
                    {"$group": {"_id": "$_id"}},
                ],
                allowDiskUse=True,
            )
        ]

        author_ids = list(
            set(author_ids_from_concepts)
            & set(author_ids_from_institutions_and_coauthors)
        )
        n_authors = len(author_ids)
        authors = mdb.alldocs.find(
            filter={"_id": {"$in": author_ids}},
            projection=["display_name"],
            sort=[("display_name", 1)],
            limit=50,
        )
    else:
        n_authors = mdb.alldocs.count_documents({"type": "Author"})
        authors = []
    all_author_concepts = list(mdb.all_author_concepts.find())
    all_work_institutions = list(mdb.all_work_institutions.find())
    all_authors = list(mdb.alldocs.find({"type": "Author"}, ["display_name"]))
    qarg_counts = {
        qarg: [i for i in qarg_values[qarg] if i]
        for qarg in ["concept", "institution", "coauthor"]
    }
    return templates.TemplateResponse(
        "funnel_authors.html",
        {
            "request": request,
            "authors": authors,
            "n_authors": n_authors,
            "concepts": tuple(qarg_values["concept"]),
            "institutions": tuple(qarg_values["institution"]),
            "coauthors": tuple(qarg_values["coauthor"]),
            "qarg_counts": qarg_counts,
            "all_author_concepts": all_author_concepts,
            "all_work_institutions": all_work_institutions,
            "all_authors": all_authors,
        },
    )


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
    author_coauthors = list(
        mdb.alldocs.aggregate(
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
    )
    author_collaborating_institutions = list(
        mdb.alldocs.aggregate(
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
    affil_collaborating_authors = list(
        mdb.alldocs.aggregate(
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

from datetime import date
from gettext import gettext, ngettext
from pathlib import Path
import re
from typing import Any, Annotated
from urllib.parse import unquote_plus

from fastapi import FastAPI, Depends, Query, Cookie, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import requests
from starlette import status
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, JSONResponse
from toolz import assoc, merge

from helioweb.infra.config import (
    HTTPS_URLS,
    ORCID_CLIENT_ID,
    ORCID_CLIENT_SECRET,
    ORCID_REDIRECT_URI,
)
from helioweb.infra.core import get_mongodb
from helioweb.ui.util import (
    raise404_if_none,
    concept_tent,
    institution_tent,
)

app = FastAPI(docs_url="/apidocs")
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).parent.joinpath("static")),
    name="static",
)
templates = Jinja2Templates(directory=Path(__file__).parent.joinpath("templates"))
templates.env.globals.update({"GLOBALS_today_year": str(date.today().year)})
templates.env.globals.update(
    {
        "GLOBALS_orcid_authorize_url": f"https://orcid.org/oauth/authorize?client_id={ORCID_CLIENT_ID}&response_type=code&scope=openid&redirect_uri={ORCID_REDIRECT_URI}"
    }
)
templates.env.add_extension("jinja2.ext.i18n")
templates.env.install_gettext_callables(gettext, ngettext)


def oax_api_link_for(id_: str):
    return id_.replace("https://openalex.org", "https://api.openalex.org")


def https_url_for(request: Request, name: str, **path_params: Any):
    http_url = request.url_for(name, **path_params)
    # Replace 'http' with 'https'
    return http_url.replace(scheme="https") if HTTPS_URLS else http_url


templates.env.globals["https_url_for"] = https_url_for


async def get_user(
    user_orcid: Annotated[str | None, Cookie()] = None,
    user_name: Annotated[str | None, Cookie()] = None,
    user_id_token: Annotated[str | None, Cookie()] = None,
):
    return (
        {
            "orcid": user_orcid,
            "name": user_name,
            "id_token": user_id_token,
        }
        if user_orcid
        else None
    )


@app.get("/", response_class=HTMLResponse)
async def read_home(request: Request, user=Depends(get_user)):
    return templates.TemplateResponse("home.html", {"request": request, "user": user})


@app.get("/orcid_code", response_class=RedirectResponse)
async def receive_orcid_code(request: Request, code: str, state: str | None = None):
    rv = requests.post(
        "https://orcid.org/oauth/token",
        data=(
            f"client_id={ORCID_CLIENT_ID}&client_secret={ORCID_CLIENT_SECRET}&"
            f"grant_type=authorization_code&code={code}&redirect_uri={ORCID_REDIRECT_URI}"
        ),
        headers={
            "Content-type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    token_response = rv.json()
    response = RedirectResponse(state or request.url_for("read_home"))
    for key in ["user_orcid", "user_name", "user_id_token"]:
        response.set_cookie(
            key=key,
            value=token_response[key.replace("user_", "")],
            max_age=2592000,
        )
    return response


@app.get("/logout", response_class=RedirectResponse)
async def logout(request: Request, state: str | None = None):
    response = RedirectResponse(state or request.url_for("read_home"))
    for key in ["user_orcid", "user_name", "user_id_token"]:
        response.set_cookie(
            key=key,
            value="",
            expires="Thu, 01 Jan 1970 00:00:00 GMT",
            max_age=-300,
        )
    return response


@app.get("/docs", response_class=HTMLResponse)
async def read_docs(request: Request, user=Depends(get_user)):
    return templates.TemplateResponse("docs.html", {"request": request, "user": user})


@app.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    q: str = "",
    t: str | None = None,
    mdb=Depends(get_mongodb),
    user=Depends(get_user),
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
        "search.html",
        {"request": request, "q": q, "results": results, "t": t, "user": user},
    )


@app.get("/funnel_authors", response_class=HTMLResponse)
async def funnel_authors(
    request: Request,
    concept: Annotated[list[str] | None, Query()] = None,
    institution: Annotated[list[str] | None, Query()] = None,
    coauthor: Annotated[list[str] | None, Query()] = None,
    mdb=Depends(get_mongodb),
    user=Depends(get_user),
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
            tent = concept_tent(qarg_values["concept"], mdb=mdb)
            authors_with_concepts_filter["outgoing.o"] = {"$in": tent}
        author_ids_from_concepts = [
            d["_id"] for d in mdb.alldocs.find(authors_with_concepts_filter)
        ]
        works_with_authors_filter = {"type": "Work", "outgoing.p": "author"}
        if any(qarg_values["institution"]):
            tent = institution_tent(qarg_values["institution"], mdb=mdb)
            works_with_authors_filter["outgoing.o"] = {"$in": tent}
        if any(qarg_values["coauthor"]):
            works_with_authors_filter["outgoing"] = {
                "$all": [
                    {"$elemMatch": {"p": "author", "o": a}}
                    for a in filter(None, qarg_values["coauthor"])
                ]
            }
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
            "user": user,
        },
    )


@app.post("/connectable-works", response_class=JSONResponse)
async def connectable_works(
    author_id: str,
    mdb=Depends(get_mongodb),
    authored_work: Annotated[str | None, Form()] = None,
):
    if not authored_work:
        return []

    all_works_author_complement = list(
        mdb.alldocs.find(
            {
                "type": "Work",
                "display_name": {
                    "$regex": unquote_plus_and_escape_parens(authored_work),
                    "$options": "i",
                },
                "outgoing": {"$not": {"$elemMatch": {"p": "author", "o": author_id}}},
            },
            ["display_name", "ads_work.year", "ads_work.bibcode"],
            limit=25,
        )
    )
    return [
        {
            "id": f"{w['_id']} author {author_id}",
            "value": f"{w['display_name']}",
        }
        for w in all_works_author_complement
    ]


def unquote_plus_and_escape_parens(s):
    """For use of `s` in a regex."""
    return unquote_plus(s).replace("(", "\\(").replace(")", "\\)")


@app.post("/connectable-concepts", response_class=JSONResponse)
async def connectable_concepts(
    author_id: str,
    mdb=Depends(get_mongodb),
    associated_concept: Annotated[str | None, Form()] = None,
):
    if not associated_concept:
        return []

    author = raise404_if_none(mdb.alldocs.find_one({"_id": author_id}, ["outgoing"]))
    author_concepts = list(
        mdb.alldocs.find(
            {
                "type": "Concept",
                "_id": {"$in": [edge["o"] for edge in author["outgoing"]]},
            },
            ["_id"],
        )
    )
    all_concepts_author_complement = list(
        mdb.alldocs.find(
            {
                "type": "Concept",
                "display_name": {
                    "$regex": unquote_plus_and_escape_parens(associated_concept),
                    "$options": "i",
                },
                "_id": {"$nin": [c["_id"] for c in author_concepts]},
            },
            ["display_name"],
            limit=25,
        )
    )
    return [
        {
            "id": f"{author_id} dcterms:relation {c['_id']}",
            "value": f"{c['display_name']}",
        }
        for c in all_concepts_author_complement
    ]


@app.get("/author:{orcid:path}", response_class=HTMLResponse)
async def author_home(
    request: Request, orcid: str, mdb=Depends(get_mongodb), user=Depends(get_user)
):
    author = raise404_if_none(mdb.alldocs.find_one({"_id": orcid}))
    author_concepts = list(
        mdb.alldocs.find(
            {
                "type": "Concept",
                "_id": {"$in": [edge["o"] for edge in author["outgoing"]]},
            }
        )
    )
    author_concept_links = {
        edge["o"]: edge
        for edge in author["outgoing"]
        if edge["p"] == "dcterms:relation"
    }
    for c in author_concepts:
        if submitter := author_concept_links[c["_id"]].get("q2"):
            c["_submitter"] = submitter
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
    work_author_links = {
        w["_id"]: edge
        for w in author_works
        for edge in w["outgoing"]
        if edge["p"] == "author"
    }
    author_works = sorted(
        author_works,
        key=lambda work: (work["ads_work"]["year"], work["display_name"]),
        reverse=True,
    )
    for w in author_works:
        if submitter := work_author_links[w["_id"]].get("q2"):
            w["_submitter"] = submitter
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
            "user": user,
            "all_works_author_complement": [],
            "all_concepts_author_complement": [],
        },
    )


@app.post("/author:{author_id:path}", response_class=RedirectResponse)
async def add_edge_to_author(
    request: Request,
    author_id: str,
    associated_concept_id: Annotated[str | None, Form()] = None,
    authored_work_id: Annotated[str | None, Form()] = None,
    mdb=Depends(get_mongodb),
    user=Depends(get_user),
):
    if not user:
        return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    raise404_if_none(mdb.alldocs.find_one({"_id": author_id}, ["_id"]))
    if associated_concept_id:
        if m := re.match(r"(\S+)\s(\S+)\s(\S+)", unquote_plus(associated_concept_id)):
            s, p, o = m.groups()
            if s != author_id or p not in ("dcterms:relation",):
                return HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="unacceptable author association",
                )
            raise404_if_none(
                mdb.alldocs.find_one({"_id": o}, ["_id"]),
                detail=f"concept {o} not found",
            )
            mdb.alldocs.update_one(
                {"_id": s},
                {
                    "$push": {
                        "outgoing": {
                            "p": p,
                            "o": o,
                            "q": 100,
                            "q2": f'https://orcid.org/{user["orcid"]}',
                        }
                    }
                },
            )
        else:
            return HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="concept association not properly formatted",
            )
    elif authored_work_id:
        if m := re.match(r"(\S+)\s(\S+)\s(\S+)", unquote_plus(authored_work_id)):
            s, p, o = m.groups()
            if o != author_id or p not in ("author",):
                return HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="unacceptable author association",
                )
            raise404_if_none(
                mdb.alldocs.find_one({"_id": s}, ["_id"]),
                detail=f"work {s} not found",
            )
            mdb.alldocs.update_one(
                {"_id": s},
                {
                    "$push": {
                        "outgoing": {
                            "p": p,
                            "o": o,
                            "q": 100,
                            "q2": f'https://orcid.org/{user["orcid"]}',
                        }
                    }
                },
            )
        else:
            return HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="work association not properly formatted",
            )
    else:
        HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="no relation asserted",
        )
    return RedirectResponse(
        request.url_for("author_home", orcid=author_id),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/work:{work_id:path}", response_class=HTMLResponse)
async def work_home(
    request: Request, work_id: str, mdb=Depends(get_mongodb), user=Depends(get_user)
):
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
            "user": user,
        },
    )


@app.get("/affil:{affil_id:path}", response_class=HTMLResponse)
async def affil_home(
    request: Request, affil_id: str, mdb=Depends(get_mongodb), user=Depends(get_user)
):
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
            "user": user,
        },
    )


@app.get("/concept:{concept_id:path}", response_class=HTMLResponse)
async def concept_home(
    request: Request, concept_id: str, mdb=Depends(get_mongodb), user=Depends(get_user)
):
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
    for a in concept_authors:
        if submitter := next(
            (edge for edge in a["outgoing"] if edge["o"] == concept_id)
        ).get("q2"):
            a["_submitter"] = submitter
    concept_oax_api_link = oax_api_link_for(concept["_id"])
    all_eligible_authors = list(
        mdb.alldocs.find(
            {
                "type": "Author",
                "outgoing": {
                    "$not": {"$elemMatch": {"p": "dcterms:relation", "o": concept_id}}
                },
            },
            ["display_name"],
        )
    )
    return templates.TemplateResponse(
        "concept.html",
        {
            "request": request,
            "concept": concept,
            "concept_oax_api_link": concept_oax_api_link,
            "concept_parents": concept_parents,
            "concept_children": concept_children,
            "concept_authors": concept_authors,
            "user": user,
            "all_author_concepts": list(mdb.all_author_concepts.find()),
            "all_eligible_authors": all_eligible_authors,
        },
    )


@app.post("/concept:{concept_id:path}", response_class=RedirectResponse)
async def add_edge_to_concept(
    request: Request,
    concept_id: str,
    associated_author: Annotated[str, Form()],
    mdb=Depends(get_mongodb),
    user=Depends(get_user),
):
    if not user:
        return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    raise404_if_none(mdb.alldocs.find_one({"_id": concept_id}, ["_id"]))
    if associated_author:
        if m := re.match(r"(\S+)\s(\S+)\s(\S+)", associated_author):
            s, p, o = m.groups()
            if o != concept_id or p not in ("dcterms:relation",):
                return HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="unacceptable author association",
                )
            raise404_if_none(
                mdb.alldocs.find_one({"_id": s}, ["_id"]),
                detail=f"author {s} not found",
            )
            mdb.alldocs.update_one(
                {"_id": s},
                {
                    "$push": {
                        "outgoing": {
                            "p": p,
                            "o": o,
                            "q": 100,
                            "q2": f'https://orcid.org/{user["orcid"]}',
                        }
                    }
                },
            )
        else:
            return HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="author association not properly formatted",
            )
    else:
        HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="no relation asserted",
        )
    return RedirectResponse(
        request.url_for("concept_home", concept_id=concept_id),
        status_code=status.HTTP_303_SEE_OTHER,
    )

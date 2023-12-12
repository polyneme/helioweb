from fastapi import HTTPException
from starlette import status
from toolz import unique, concat


def raise404_if_none(doc, detail="Not found"):
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    return doc


def concept_tent(concept_ids, mdb=None):
    rv = []
    for cid in [i for i in concept_ids if i]:
        rv.append(
            [
                d["_id"]
                for d in mdb.alldocs.aggregate(
                    [
                        {"$match": {"_id": cid}},
                        {
                            "$graphLookup": {
                                "from": "alldocs",
                                "startWith": "$_id",
                                "connectFromField": "_id",
                                "connectToField": "outgoing.o",
                                "restrictSearchWithMatch": {"type": "Concept"},
                                "as": "descendant_concepts",
                            }
                        },
                        {
                            "$project": {
                                "_id": 0,
                                "concept_tent": {
                                    "$concatArrays": [
                                        "$descendant_concepts",
                                        [{"_id": "$_id"}],
                                    ]
                                },
                            }
                        },
                        {
                            "$unwind": {
                                "path": "$concept_tent",
                            }
                        },
                        {
                            "$project": {
                                "_id": "$concept_tent._id",
                            }
                        },
                        {
                            "$group": {
                                "_id": "$_id",
                            }
                        },
                    ],
                    allowDiskUse=True,
                )
            ]
        )
    return list(unique(concat(rv)))


def concept_transitive_closure(cid, mdb=None):
    """Return concept IDs for all ancestors of concept ID `cid`, including `cid`."""
    return [
        d["_id"]
        for d in mdb.alldocs.aggregate(
            [
                {"$match": {"_id": cid}},
                {
                    "$lookup": {
                        # no need for $graphLookup
                        #   because skos:braoderTransitive relations are materialized in `outgoing.o`
                        "from": "alldocs",
                        "localField": "outgoing.o",
                        "foreignField": "_id",
                        "as": "ancestor_concepts",
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "concept_closure": {
                            "$concatArrays": ["$ancestor_concepts", [{"_id": "$_id"}]]
                        },
                    }
                },
                {
                    "$unwind": {
                        "path": "$concept_closure",
                    }
                },
                {
                    "$project": {
                        "_id": "$concept_closure._id",
                    }
                },
                {
                    "$group": {
                        "_id": "$_id",
                    }
                },
            ]
        )
    ]


def institution_tent(institution_ids, mdb=None):
    rv = []
    for iid in [i for i in institution_ids if i]:
        rv.append(
            [
                d["_id"]
                for d in mdb.alldocs.aggregate(
                    [
                        {"$match": {"_id": iid}},
                        {
                            "$graphLookup": {
                                "from": "alldocs",
                                "startWith": "$_id",
                                "connectFromField": "_id",
                                "connectToField": "outgoing.o",
                                "restrictSearchWithMatch": {"type": "Institution"},
                                "as": "descendant_institutions",
                            }
                        },
                        {
                            "$project": {
                                "_id": 0,
                                "institution_tent": {
                                    "$concatArrays": [
                                        "$descendant_institutions",
                                        [{"_id": "$_id"}],
                                    ]
                                },
                            }
                        },
                        {
                            "$unwind": {
                                "path": "$institution_tent",
                            }
                        },
                        {
                            "$project": {
                                "_id": "$institution_tent._id",
                            }
                        },
                        {
                            "$group": {
                                "_id": "$_id",
                            }
                        },
                    ],
                    allowDiskUse=True,
                )
            ]
        )
    return list(unique(concat(rv)))


def institution_transitive_closure(iid, mdb=None):
    return [
        d["_id"]
        for d in mdb.alldocs.aggregate(
            [
                {"$match": {"_id": iid}},
                {
                    # institution hierarchy is currently just one-deep, so $lookup would currently work as well.
                    "$graphLookup": {
                        "from": "alldocs",
                        "startWith": "$outgoing.o",
                        "connectFromField": "outgoing.o",
                        "connectToField": "_id",
                        "as": "ancestor_institutions",
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "institution_closure": {
                            "$concatArrays": [
                                "$ancestor_institutions",
                                [{"_id": "$_id"}],
                            ]
                        },
                    }
                },
                {
                    "$unwind": {
                        "path": "$institution_closure",
                    }
                },
                {
                    "$project": {
                        "_id": "$institution_closure._id",
                    }
                },
                {
                    "$group": {
                        "_id": "$_id",
                    }
                },
            ]
        )
    ]

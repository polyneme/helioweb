from fastapi import HTTPException
from starlette import status


def raise404_if_none(doc, detail="Not found"):
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    return doc

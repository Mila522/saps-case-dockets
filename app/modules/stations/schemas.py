import uuid

from pydantic import BaseModel


class InvestigatorOut(BaseModel):
    id: uuid.UUID
    service_number: str
    rank: str
    username: str

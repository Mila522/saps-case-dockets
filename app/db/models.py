"""Import every model module to register all tables with Base.metadata."""
from app.modules.access import models as access_models
from app.modules.stations import models as station_models
from app.modules.complainants import models as complainant_models
from app.modules.complaints import models as complaint_models
from app.modules.refusals import models as refusal_models
from app.modules.dockets import models as docket_models
from app.modules.investigations import models as investigation_models
from app.modules.evidence import models as evidence_models

from app.modules.communications import models as communication_models
from app.modules.alerts import models as alert_models
from app.modules.audit import models as audit_models
from app.modules.system import models as system_models
from app.modules.authentication import models as authentication_models

__all__ = [
    "access_models", "station_models", "complainant_models", "complaint_models",
    "refusal_models", "docket_models", "investigation_models", "evidence_models",
    "communication_models", "alert_models", "audit_models", "system_models", "authentication_models",
]

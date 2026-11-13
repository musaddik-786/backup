"""
Controlled vocabulary for the Knowledge Ops KB.

This is the "human-defined taxonomy" referenced in the architecture diagram
(Ingestion & Normalization box: "Standardizes — Tags every entry to a
predefined taxonomy (module, category, severity, resolution type)").

Every record in the Knowledge Store MUST be tagged against these enums.
Nothing else is allowed through — this is what keeps retrieval consistent
across all four downstream tier agents (L1-L4).
"""

from enum import Enum


class GuidewireModule(str, Enum):
    POLICY_CENTER = "PolicyCenter"
    CLAIM_CENTER = "ClaimCenter"
    BILLING_CENTER = "BillingCenter"
    CONTACT_MANAGER = "ContactManager"
    RATING_GOSU = "Rating/Gosu"
    INTEGRATION_GATEWAY = "IntegrationGateway"
    DIGITAL_PORTAL = "DigitalPortal"
    DATA_PLATFORM = "DataPlatform"


class IssueCategory(str, Enum):
    PERFORMANCE_DEGRADATION = "PerformanceDegradation"
    INTEGRATION_FAILURE = "IntegrationFailure"
    DATA_CORRUPTION = "DataCorruption"
    CONFIGURATION_ERROR = "ConfigurationError"
    UPGRADE_REGRESSION = "UpgradeRegression"
    AUTHENTICATION_ACCESS = "AuthenticationAccess"
    BATCH_JOB_FAILURE = "BatchJobFailure"
    UI_RENDERING_BUG = "UIRenderingBug"
    LICENSING = "Licensing"


class Severity(str, Enum):
    SEV1_CRITICAL = "SEV1-Critical"      # production down / major incident
    SEV2_HIGH = "SEV2-High"              # major feature broken, workaround exists
    SEV3_MEDIUM = "SEV3-Medium"          # degraded but usable
    SEV4_LOW = "SEV4-Low"                # cosmetic / minor


class ResolutionType(str, Enum):
    CONFIG_CHANGE = "ConfigChange"
    CODE_FIX = "CodeFix"
    PATCH_APPLIED = "PatchApplied"
    RESTART_RECYCLE = "RestartRecycle"
    DATA_REMEDIATION = "DataRemediation"
    VENDOR_ESCALATION = "VendorEscalation"
    WORKAROUND = "Workaround"
    USER_ERROR_CLOSED = "UserErrorClosed"
    NO_ACTION_BY_DESIGN = "NoActionByDesign"


class SourceType(str, Enum):
    TICKET = "HistoricalTicket"
    CHAT_RCA = "ChatOrRCAReport"
    RELEASE_NOTE = "GuidewireReleaseNote"
    RUNBOOK = "RunbookOrSOP"


class CurationStatus(str, Enum):
    PENDING_REVIEW = "PendingReview"
    AUTO_APPROVED = "AutoApproved"       # high-confidence tagging, no SME needed
    SME_APPROVED = "SMEApproved"
    SME_REJECTED = "SMERejected"
    NEEDS_RETAG = "NeedsRetag"


# Confidence threshold below which a record is routed to the SME Curation
# Gate instead of being auto-approved into the Knowledge Store.
AUTO_APPROVE_CONFIDENCE_THRESHOLD = 0.72

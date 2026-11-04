from app.social.providers.base import ProviderError, PublishResult
from app.social.providers.youtube import YouTubeProvider

PROVIDERS = {"youtube": YouTubeProvider()}
PROVIDER_CATALOG = (
    ("youtube", "YouTube", True, True, True,
     "YouTube OAuth and Data API adapter implemented. Requires a Google OAuth client, enabled YouTube Data API, channel eligibility, and possible Google verification for public users."),
    ("instagram", "Instagram", False, False, True,
     "Not implemented. Official publishing requires eligible Instagram professional accounts and Meta app permissions/review."),
    ("tiktok", "TikTok", False, False, True,
     "Not implemented. Requires approved TikTok developer/app products and publishing permissions."),
    ("facebook", "Facebook Pages / Reels", False, False, True,
     "Not implemented. Requires a Meta app, Page permissions, and applicable app review."),
    ("linkedin", "LinkedIn", False, False, True,
     "Not implemented. Requires approved LinkedIn developer products and member/organization permissions."),
    ("x", "X", False, False, True,
     "Not implemented. Requires approved X API access and applicable plan/permissions."),
    ("pinterest", "Pinterest", False, False, True,
     "Not implemented. Requires approved Pinterest developer access and publishing scopes."),
)


def get_provider(slug: str):
    return PROVIDERS.get(slug)


def seed_provider_catalog(db) -> None:
    from app.models.models import SocialProvider
    for slug, name, oauth, publishing, approval, note in PROVIDER_CATALOG:
        if not db.query(SocialProvider.id).filter_by(slug=slug).first():
            db.add(SocialProvider(slug=slug, display_name=name, enabled=True, oauth_supported=oauth,
                                  publishing_supported=publishing, approval_required=approval,
                                  availability_note=note, limits_json={}))
    db.flush()


__all__ = ["ProviderError", "PublishResult", "YouTubeProvider", "PROVIDERS", "PROVIDER_CATALOG", "get_provider"]

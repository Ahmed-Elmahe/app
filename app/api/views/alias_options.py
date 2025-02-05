from flask import jsonify, request, g
from sqlalchemy import desc

from app.api.base import api_bp, require_api_auth
from app.db import Session
from app.log import LOG
from app.models import AliasUsedOn, Alias
from app.utils import get_hostname_prefix, get_alias_suffixes

def _get_alias_options(user, hostname):
    """Shared logic for both API versions"""
    ret = {
        "can_create": user.can_create_new_alias(),
        "prefix_suggestion": get_hostname_prefix(hostname) if hostname else "",
        "recommendation": None
    }

    if hostname:
        # Get recommended alias in single query
        alias = (Session.query(Alias)
                 .join(AliasUsedOn)
                 .filter(
                     Alias.user_id == user.id,
                     AliasUsedOn.hostname == hostname)
                 .order_by(desc(AliasUsedOn.created_at))
                 .first())

        if alias:
            LOG.d("Found alias %s for %s", alias, hostname)
            ret["recommendation"] = {"alias": alias.email, "hostname": hostname}

    return ret

@api_bp.route("/v4/alias/options")
@require_api_auth
def options_v4():
    response = _get_alias_options(g.user, request.args.get("hostname"))
    suffixes = get_alias_suffixes(g.user)
    response["suffixes"] = [[s.suffix, s.signed_suffix] for s in suffixes]
    return jsonify(response)

@api_bp.route("/v5/alias/options")
@require_api_auth
def options_v5():
    response = _get_alias_options(g.user, request.args.get("hostname"))
    response["suffixes"] = [{
        "suffix": s.suffix,
        "signed_suffix": s.signed_suffix,
        "is_custom": s.is_custom,
        "is_premium": s.is_premium
    } for s in get_alias_suffixes(g.user)]

    return jsonify(response)

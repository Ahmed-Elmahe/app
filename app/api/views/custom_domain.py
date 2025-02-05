from flask import g, request, jsonify
from app.api.base import api_bp, require_api_auth
from app.custom_domain_utils import set_custom_domain_mailboxes
from app.db import Session
from app.log import LOG
from app.models import CustomDomain, DomainDeletedAlias


def custom_domain_to_dict(custom_domain: CustomDomain):
    """Convert a CustomDomain object to a dictionary."""
    return {
        "id": custom_domain.id,
        "domain_name": custom_domain.domain,
        "is_verified": custom_domain.verified,
        "nb_alias": custom_domain.nb_alias(),
        "creation_date": custom_domain.created_at.format(),
        "creation_timestamp": custom_domain.created_at.timestamp,
        "catch_all": custom_domain.catch_all,
        "name": custom_domain.name,
        "random_prefix_generation": custom_domain.random_prefix_generation,
        "mailboxes": [{"id": mb.id, "email": mb.email} for mb in custom_domain.mailboxes],
    }


@api_bp.route("/custom_domains", methods=["GET"])
@require_api_auth
def get_custom_domains():
    """Get all custom domains for the authenticated user."""
    user = g.user
    custom_domains = CustomDomain.filter_by(user_id=user.id, is_sl_subdomain=False).all()
    return jsonify(custom_domains=[custom_domain_to_dict(cd) for cd in custom_domains])


@api_bp.route("/custom_domains/<int:custom_domain_id>/trash", methods=["GET"])
@require_api_auth
def get_custom_domain_trash(custom_domain_id: int):
    """Get deleted aliases for a specific custom domain."""
    user = g.user
    custom_domain = CustomDomain.get(custom_domain_id)

    if not custom_domain or custom_domain.user_id != user.id:
        return jsonify(error="Forbidden"), 403

    domain_deleted_aliases = DomainDeletedAlias.filter_by(domain_id=custom_domain.id).all()
    return jsonify(
        aliases=[
            {
                "alias": dda.email,
                "deletion_timestamp": dda.created_at.timestamp,
            }
            for dda in domain_deleted_aliases
        ]
    )


@api_bp.route("/custom_domains/<int:custom_domain_id>", methods=["PATCH"])
@require_api_auth
def update_custom_domain(custom_domain_id):
    """
    Update a custom domain.
    Input:
        custom_domain_id: in URL
    In body:
        catch_all (optional): boolean
        random_prefix_generation (optional): boolean
        name (optional): string
        mailbox_ids (optional): array of integers
    Output:
        200: Success
        400: Invalid request
        403: Forbidden
    """
    data = request.get_json()
    if not data:
        return jsonify(error="Request body cannot be empty"), 400

    user = g.user
    custom_domain: CustomDomain = CustomDomain.get(custom_domain_id)

    if not custom_domain or custom_domain.user_id != user.id:
        return jsonify(error="Forbidden"), 403

    changed = False

    # Update catch_all
    if "catch_all" in data:
        if not isinstance(data["catch_all"], bool):
            return jsonify(error="catch_all must be a boolean"), 400
        custom_domain.catch_all = data["catch_all"]
        changed = True

    # Update random_prefix_generation
    if "random_prefix_generation" in data:
        if not isinstance(data["random_prefix_generation"], bool):
            return jsonify(error="random_prefix_generation must be a boolean"), 400
        custom_domain.random_prefix_generation = data["random_prefix_generation"]
        changed = True

    # Update name
    if "name" in data:
        if not isinstance(data["name"], str):
            return jsonify(error="name must be a string"), 400
        custom_domain.name = data["name"]
        changed = True

    # Update mailboxes
    if "mailbox_ids" in data:
        if not isinstance(data["mailbox_ids"], list) or not all(
            isinstance(m_id, int) for m_id in data["mailbox_ids"]
        ):
            return jsonify(error="mailbox_ids must be an array of integers"), 400

        result = set_custom_domain_mailboxes(user.id, custom_domain, data["mailbox_ids"])
        if not result.success:
            LOG.info(
                f"Prevented from updating mailboxes [user_id={user.id}, custom_domain_id={custom_domain.id}]: {result.reason.value}"
            )
            return jsonify(error=result.reason.value), 400
        changed = True

    # Commit changes if any
    if changed:
        Session.commit()

    # Return updated custom domain
    custom_domain = CustomDomain.get(custom_domain_id)
    return jsonify(custom_domain=custom_domain_to_dict(custom_domain)), 200

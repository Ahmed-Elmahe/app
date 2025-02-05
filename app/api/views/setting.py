import arrow
from flask import jsonify, g, request
from app.api.base import api_bp, require_api_auth
from app.db import Session
from app.log import LOG
from app.models import (
    User,
    AliasGeneratorEnum,
    SLDomain,
    CustomDomain,
    SenderFormatEnum,
    AliasSuffixEnum,
)
from app.proton.proton_unlink import perform_proton_account_unlink


def setting_to_dict(user: User):
    """Convert user settings to a dictionary for API response."""
    return {
        "notification": user.notification,
        "alias_generator": "word" if user.alias_generator == AliasGeneratorEnum.word.value else "uuid",
        "random_alias_default_domain": user.default_random_alias_domain(),
        "sender_format": SenderFormatEnum.get_name(user.sender_format) or SenderFormatEnum.AT.name,
        "random_alias_suffix": AliasSuffixEnum.get_name(user.random_alias_suffix),
    }


@api_bp.route("/setting", methods=["GET"])
@require_api_auth
def get_setting():
    """
    Retrieve user settings.
    Output:
        - notification: bool
        - alias_generator: word|uuid
        - random_alias_default_domain: str
        - sender_format: str
        - random_alias_suffix: str
    """
    user = g.user
    return jsonify(setting_to_dict(user))


def validate_alias_generator(alias_generator):
    """Validate and convert alias_generator input."""
    if alias_generator not in ["word", "uuid"]:
        return jsonify(error="Invalid alias_generator"), 400
    return AliasGeneratorEnum.word.value if alias_generator == "word" else AliasGeneratorEnum.uuid.value


def validate_sender_format(sender_format):
    """Validate sender_format input."""
    if not SenderFormatEnum.has_name(sender_format):
        return jsonify(error="Invalid sender_format"), 400
    return SenderFormatEnum.get_value(sender_format)


def validate_random_alias_suffix(random_alias_suffix):
    """Validate random_alias_suffix input."""
    if not AliasSuffixEnum.has_name(random_alias_suffix):
        return jsonify(error="Invalid random_alias_suffix"), 400
    return AliasSuffixEnum.get_value(random_alias_suffix)


def validate_default_domain(user, default_domain):
    """Validate default domain input, checking SL and custom domains."""
    sl_domain = SLDomain.get_by(domain=default_domain)
    if sl_domain:
        if sl_domain.premium_only and not user.is_premium():
            return jsonify(error="You cannot use this domain"), 400
        return {"default_alias_public_domain_id": sl_domain.id, "default_alias_custom_domain_id": None}

    custom_domain = CustomDomain.get_by(domain=default_domain)
    if not custom_domain or custom_domain.user_id != user.id or not custom_domain.verified:
        LOG.w("%s cannot use domain %s", user, default_domain)
        return jsonify(error="Invalid domain"), 400

    return {"default_alias_custom_domain_id": custom_domain.id, "default_alias_public_domain_id": None}


@api_bp.route("/setting", methods=["PATCH"])
@require_api_auth
def update_setting():
    """
    Update user settings.
    Input:
        - notification: bool
        - alias_generator: word|uuid
        - sender_format: str
        - random_alias_default_domain: str
        - random_alias_suffix: str
    """
    user = g.user
    data = request.get_json() or {}

    # Update notification setting
    if "notification" in data:
        user.notification = data["notification"]

    # Update alias_generator
    if "alias_generator" in data:
        alias_generator_value = validate_alias_generator(data["alias_generator"])
        if isinstance(alias_generator_value, tuple):
            return alias_generator_value  # Return the error response
        user.alias_generator = alias_generator_value

    # Update sender_format
    if "sender_format" in data:
        sender_format_value = validate_sender_format(data["sender_format"])
        if isinstance(sender_format_value, tuple):
            return sender_format_value  # Return the error response
        user.sender_format = sender_format_value
        user.sender_format_updated_at = arrow.now()

    # Update random_alias_suffix
    if "random_alias_suffix" in data:
        random_alias_suffix_value = validate_random_alias_suffix(data["random_alias_suffix"])
        if isinstance(random_alias_suffix_value, tuple):
            return random_alias_suffix_value  # Return the error response
        user.random_alias_suffix = random_alias_suffix_value

    # Update random_alias_default_domain
    if "random_alias_default_domain" in data:
        domain_validation = validate_default_domain(user, data["random_alias_default_domain"])
        if isinstance(domain_validation, tuple):
            return domain_validation  # Return the error response
        user.default_alias_public_domain_id = domain_validation["default_alias_public_domain_id"]
        user.default_alias_custom_domain_id = domain_validation["default_alias_custom_domain_id"]

    Session.commit()
    return jsonify(setting_to_dict(user))


@api_bp.route("/setting/domains", methods=["GET"])
@require_api_auth
def get_available_domains_for_random_alias():
    """
    Retrieve available domains for creating random aliases.
    Output: List of (is_sl, domain)
    """
    user = g.user
    domains = [(is_sl, domain) for is_sl, domain in user.available_domains_for_random_alias()]
    return jsonify(domains)


@api_bp.route("/v2/setting/domains", methods=["GET"])
@require_api_auth
def get_available_domains_for_random_alias_v2():
    """
    Retrieve available domains for creating random aliases (v2 format).
    Output: List of domains with 'domain' and 'is_custom' flags.
    """
    user = g.user
    domains = [{"domain": domain, "is_custom": not is_sl} for is_sl, domain in user.available_domains_for_random_alias()]
    return jsonify(domains)


@api_bp.route("/setting/unlink_proton_account", methods=["DELETE"])
@require_api_auth
def unlink_proton_account():
    """
    Unlink the ProtonMail account from the user.
    Output:
        - {"ok": True} on success
        - error message on failure
    """
    user = g.user
    if not perform_proton_account_unlink(user):
        return jsonify(error="The account cannot be unlinked"), 400
    return jsonify({"ok": True})

from flask import jsonify, g
from sqlalchemy_utils.types.arrow import arrow

from app.api.base import api_bp, require_api_sudo, require_api_auth
from app import config
from app.extensions import limiter
from app.log import LOG
from app.models import Job, ApiToCookieToken
from app.user_audit_log_utils import emit_user_audit_log, UserAuditLogAction
from app.utils import HTTP_STATUS_OK, HTTP_STATUS_SERVER_ERROR

# Constants for log messages
LOG_DELETE_USER = "User %s (%s) marked for deletion from API"
LOG_DELETE_JOB_FAIL = "Failed to schedule delete account job for user %s"
LOG_DELETE_JOB_SUCCESS = "Scheduled delete account job for user %s"

@api_bp.route("/user", methods=["DELETE"])
@require_api_sudo
def delete_user():
    """
    Delete the current user. Requires sudo mode.

    This API schedules a job for deleting the user account.
    """
    user = g.user

    # Emit an audit log for the deletion request
    emit_user_audit_log(
        user=user,
        action=UserAuditLogAction.UserMarkedForDeletion,
        message=LOG_DELETE_USER % (user.id, user.email),
    )

    # Log the deletion attempt
    LOG.info(LOG_DELETE_USER % (user.id, user.email))

    try:
        # Schedule a job to delete the user account
        Job.create(
            name=config.JOB_DELETE_ACCOUNT,
            payload={"user_id": user.id},
            run_at=arrow.now(),
            commit=True,
        )
        LOG.info(LOG_DELETE_JOB_SUCCESS % user.id)
        return jsonify({"ok": True}), HTTP_STATUS_OK

    except Exception as e:
        # Log failure and return an error response
        LOG.error(LOG_DELETE_JOB_FAIL % user.id, exc_info=e)
        return jsonify({"error": "Failed to schedule delete job"}), HTTP_STATUS_SERVER_ERROR


@api_bp.route("/user/cookie_token", methods=["GET"])
@require_api_auth
@limiter.limit("5/minute")
def get_api_session_token():
    """
    Generate and return a temporary token that can be exchanged for a cookie-based session.

    Rate-limited to 5 requests per minute.

    Output:
        200 and a token:
        {
            "token": "random_temp_token_string",
        }
    """
    user = g.user
    api_key = g.api_key

    try:
        # Generate a token to allow cookie session creation
        token = ApiToCookieToken.create(
            user=user,
            api_key_id=api_key.id,
            commit=True,
        )
        LOG.info("Generated API session token for user %s", user.id)
        return jsonify({"token": token.code}), HTTP_STATUS_OK

    except Exception as e:
        # Log failure and return an error response
        LOG.error("Failed to generate API session token for user %s", user.id, exc_info=e)
        return jsonify({"error": "Failed to generate session token"}), HTTP_STATUS_SERVER_ERROR

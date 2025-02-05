from flask import g, jsonify
from app.api.base import api_bp, require_api_auth
from app.models import Alias, Client, CustomDomain
from app.alias_utils import alias_export_csv
from app.log import LOG


@api_bp.route("/export/data", methods=["GET"])
@require_api_auth
def export_data():
    """
    Get user data
    Output:
        Alias, custom domain, and app info
    """
    user = g.user

    try:
        # Prepare data for export
        data = {
            "email": user.email,
            "name": user.name,
            "aliases": [
                {"email": alias.email, "enabled": alias.enabled}
                for alias in Alias.filter_by(user_id=user.id).all()
            ],
            "apps": [
                {"name": app.name, "home_url": app.home_url}
                for app in Client.filter_by(user_id=user.id)
            ],
            "custom_domains": [
                custom_domain.domain
                for custom_domain in CustomDomain.filter_by(user_id=user.id).all()
            ],
        }

        LOG.d(f"Exported data for user {user.email}")
        return jsonify(data), 200

    except Exception as e:
        LOG.e(f"Error exporting data for user {user.email}: {str(e)}")
        return jsonify(error="An error occurred while exporting data"), 500


@api_bp.route("/export/aliases", methods=["GET"])
@require_api_auth
def export_aliases():
    """
    Get user aliases as an importable CSV file
    Output:
        Importable CSV file
    """
    user = g.user

    try:
        LOG.d(f"Exported aliases for user {user.email}")
        return alias_export_csv(user)

    except Exception as e:
        LOG.e(f"Error exporting aliases for user {user.email}: {str(e)}")
        return jsonify(error="An error occurred while exporting aliases"), 500

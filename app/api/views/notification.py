from flask import g, request, jsonify
from app.api.base import api_bp, require_api_auth
from app.config import PAGE_LIMIT
from app.db import Session
from app.models import Notification
from app.log import LOG


@api_bp.route("/notifications", methods=["GET"])
@require_api_auth
def get_notifications():
    """
    Get notifications

    Input:
    - page: in url. Starts at 0

    Output:
    - more: boolean. Whether there's more notification to load
    - notifications: list of notifications.
        - id
        - message
        - title
        - read
        - created_at
    """
    user = g.user

    # Validate page parameter
    try:
        page = int(request.args.get("page", 0))
        if page < 0:
            raise ValueError("Page must be a non-negative integer")
    except (ValueError, TypeError):
        LOG.e(f"Invalid page parameter for user {user.email}")
        return jsonify(error="Page must be a non-negative integer"), 400

    # Fetch notifications
    notifications = (
        Notification.filter_by(user_id=user.id)
        .order_by(Notification.read, Notification.created_at.desc())
        .limit(PAGE_LIMIT + 1)  # Load one extra record to check if there's more
        .offset(page * PAGE_LIMIT)
        .all()
    )

    have_more = len(notifications) > PAGE_LIMIT

    # Prepare response
    response = {
        "more": have_more,
        "notifications": [
            {
                "id": notification.id,
                "message": notification.message,
                "title": notification.title,
                "read": notification.read,
                "created_at": notification.created_at.humanize(),
            }
            for notification in notifications[:PAGE_LIMIT]
        ],
    }

    LOG.d(f"Fetched notifications for user {user.email}")
    return jsonify(response), 200


@api_bp.route("/notifications/<int:notification_id>/read", methods=["POST"])
@require_api_auth
def mark_as_read(notification_id):
    """
    Mark a notification as read
    Input:
        notification_id: in url
    Output:
        200 if updated successfully
    """
    user = g.user
    notification = Notification.get(notification_id)

    # Validate notification
    if not notification or notification.user_id != user.id:
        LOG.e(f"Unauthorized access to notification {notification_id} by user {user.email}")
        return jsonify(error="Forbidden"), 403

    # Mark as read
    notification.read = True
    Session.commit()

    LOG.d(f"Marked notification {notification_id} as read for user {user.email}")
    return jsonify(done=True), 200

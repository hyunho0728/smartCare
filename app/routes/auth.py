from flask import Blueprint, jsonify, redirect, render_template, session
from services.auth_service import current_role, DESTINATIONS

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login')
def login_view():
    role = current_role()
    if role:
        return redirect(DESTINATIONS[role])
    return render_template('login.html')


@auth_bp.route('/api/auth/session')
def auth_session():
    role = current_role()
    return jsonify(valid=bool(role), role=role, destination=DESTINATIONS.get(role, '/login'))


@auth_bp.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    session.clear()
    return jsonify(success=True, message='로그아웃 되었습니다.')

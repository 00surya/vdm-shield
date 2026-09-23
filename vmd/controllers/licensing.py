from flask import Blueprint, current_app, jsonify, render_template, request
from ..licensing import LicenceError

licensing = Blueprint('licensing', __name__)


@licensing.get('/activation')
def activation_page():
    return render_template('activation.html', cloud_url=current_app.extensions['vdm_licence'].server)


@licensing.get('/api/licence')
def licence_status():
    return jsonify(current_app.extensions['vdm_licence'].status())


@licensing.post('/api/licence/<action>')
def licence_action(action):
    client = current_app.extensions['vdm_licence']
    try:
        if action == 'activate':
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                return jsonify(error='Enter an activation code.'), 400
            client.activate(data.get('code'))
        elif action == 'check':
            client.renew()
        elif action == 'clear':
            client.clear()
        else:
            return jsonify(error='Unknown action.'), 404
        return jsonify(client.status())
    except (LicenceError, KeyError, TypeError, ValueError) as exc:
        return jsonify(error=str(exc) if isinstance(exc, LicenceError) else 'Invalid licence server response.'), 400

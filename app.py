from flask import Flask, render_template, request, redirect, url_for, session, flash
import joblib
import numpy as np
import pandas as pd
import os
import requests
from datetime import datetime
from supabase import create_client

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'change-this-to-a-long-random-string')

BASE = os.path.dirname(os.path.abspath(__file__))
model_fc = joblib.load(os.path.join(BASE, 'models', 'model_forecast.pkl'))
model_arima = joblib.load(os.path.join(BASE, 'models', 'model_arima.pkl'))
df = pd.read_csv(os.path.join(BASE, 'mumbai_master_dataset.csv'))

SUPABASE_URL = os.environ.get('SUPABASE_URL', 'https://keslogxobehjuliuvrxk.supabase.co')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', 'sb_publishable_JTNmXo5RZQD4SU_Htyvg2Q_6BgzJE1v')
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

USERS = {'admin': 'waterwatch123', 'aabid': 'mumbai2025'}

MONTHS = ['January','February','March','April','May','June','July',
          'August','September','October','November','December']
FEATURES = ["cur_reservoir", "origin_month", "horizon", "cur_rain", "cur_temp", "cur_hum", "consumption"]


def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            flash('Please login first.')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def severity_label(pct):
    if pct < 20:
        return 'Severe'
    if pct <= 50:
        return 'Moderate'
    return 'Mild'


def plain_message(sev):
    return {
        'Severe': 'Water stock is expected to be very low. Shortage risk is high, so water use should be kept to a minimum.',
        'Moderate': 'Water stock is expected to be below half. Some shortage is possible, so use water carefully.',
        'Mild': 'Water stock is expected to be healthy. No major shortage is expected.',
    }[sev]


def get_live_reservoir():
    """Combined lake level from Supabase (the scraped/updated data)."""
    try:
        rows = supabase.table('lake_levels').select('*').execute().data
        if rows:
            return float(rows[0]['combined_pct']), rows[0]['last_updated'], True
    except Exception:
        pass
    return float(df.iloc[-1]['reservoir_pct']), 'latest dataset month', False


def get_recent_weather(month):
    """Last ~30 days of weather for Mumbai from Open-Meteo; falls back to typical values."""
    clim = df[df['month'] == month]
    fallback = {'rain': round(float(clim['rainfall_mm'].mean()), 1),
                'temp': round(float(clim['avg_temp'].mean()), 1),
                'hum': round(float(clim['avg_humidity'].mean()), 1),
                'live': False}
    try:
        r = requests.get('https://api.open-meteo.com/v1/forecast', params={
            'latitude': 19.0760, 'longitude': 72.8777,
            'daily': 'temperature_2m_mean,relative_humidity_2m_mean,precipitation_sum',
            'past_days': 30, 'forecast_days': 1, 'timezone': 'Asia/Kolkata'}, timeout=8)
        d = r.json()['daily']
        temp = [x for x in d['temperature_2m_mean'] if x is not None]
        hum = [x for x in d['relative_humidity_2m_mean'] if x is not None]
        rain = [x for x in d['precipitation_sum'] if x is not None]
        if len(temp) >= 20 and len(hum) >= 20:
            return {'rain': round(sum(rain), 1),
                    'temp': round(sum(temp) / len(temp), 1),
                    'hum': round(sum(hum) / len(hum), 1),
                    'live': True}
    except Exception:
        pass
    return fallback


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username in USERS and USERS[username] == password:
            session['user'] = username
            return redirect(url_for('home'))
        flash('Invalid username or password.')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/')
@login_required
def home():
    reservoir, _, _ = get_live_reservoir()
    latest = df.iloc[-1]
    return render_template('index.html',
        reservoir=round(reservoir, 2),
        rainfall=round(latest['rainfall_mm'], 2),
        temp=round(latest['avg_temp'], 2),
        humidity=round(latest['avg_humidity'], 2),
        severity=severity_label(reservoir),
        consumption_mld=int(latest['consumption_mld']),
        user=session['user'])


@app.route('/predict', methods=['GET', 'POST'])
@login_required
def predict():
    now = datetime.now()
    options = []
    for h in range(1, 13):
        total = now.year * 12 + (now.month - 1) + h
        options.append({'h': h, 'label': f"{MONTHS[total % 12]} {total // 12}"})

    if request.method == 'POST':
        h = max(1, min(12, int(request.form['horizon'])))
        total = now.year * 12 + (now.month - 1) + h
        ty, tm = total // 12, total % 12 + 1

        cur_res, updated, live_res = get_live_reservoir()
        wx = get_recent_weather(now.month)
        cons = int(df.iloc[-1]['consumption_mld'])

        X = pd.DataFrame([[cur_res, now.month, h, wx['rain'], wx['temp'], wx['hum'], cons]], columns=FEATURES)
        pred = float(model_fc.predict(X)[0])
        tree_preds = np.array([t.predict(X.values)[0] for t in model_fc.estimators_])
        lo, hi = np.percentile(tree_preds, [10, 90])
        pred, lo, hi = [round(float(np.clip(v, 0, 100)), 1) for v in (pred, lo, hi)]

        # ARIMA: history-only seasonal outlook for the same month (data ends Dec 2025)
        steps = (ty - 2025) * 12 + tm - 12
        arima_val = None
        if steps >= 1:
            arima_val = round(float(np.clip(model_arima.predict(n_periods=steps)[-1], 0, 100)), 1)

        sev = severity_label(pred)
        session['forecast'] = {
            'target_label': f"{MONTHS[tm - 1]} {ty}", 'pred': pred, 'lo': lo, 'hi': hi,
            'severity': sev, 'message': plain_message(sev),
            'cur_reservoir': round(cur_res, 2), 'updated': updated, 'live_res': live_res,
            'weather': wx, 'consumption': cons, 'arima': arima_val, 'horizon': h}
        return redirect(url_for('result'))

    cur_res, updated, live_res = get_live_reservoir()
    return render_template('predict.html', options=options, live_reservoir=round(cur_res, 2),
                           updated=updated, live_res=live_res, user=session.get('user'))


@app.route('/result')
@login_required
def result():
    f = session.get('forecast')
    if not f:
        return redirect(url_for('predict'))
    return render_template('result.html', f=f, user=session.get('user'))


@app.route('/livelevels')
@login_required
def livelevels():
    try:
        rows = supabase.table('lake_levels').select('*').execute().data
        lake_data = [{'name': r['lake_name'], 'pct': r['percentage']} for r in rows]
        combined = rows[0]['combined_pct'] if rows else None
        last_updated = rows[0]['last_updated'] if rows else None
        error = None
    except Exception:
        lake_data, combined, last_updated = [], None, None
        error = 'Could not fetch data from database.'
    return render_template('livelevels.html', lake_data=lake_data, combined=combined,
                           last_updated=last_updated, error=error, user=session.get('user'))


@app.route('/datadive')
@login_required
def datadive():
    stats = df.describe().round(2).to_html(classes='table table-striped')
    return render_template('datadive.html', stats=stats, user=session.get('user'))


@app.route('/about')
@login_required
def about():
    return render_template('about.html', user=session.get('user'))


@app.route('/weather')
@login_required
def weather():
    return render_template('weather.html', user=session.get('user'))


@app.route('/climate')
@login_required
def climate():
    return render_template('climate.html', user=session.get('user'))


@app.route('/education')
@login_required
def education():
    return render_template('education.html', user=session.get('user'))


if __name__ == '__main__':
    app.run(debug=True)
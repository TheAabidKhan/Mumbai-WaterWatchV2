from flask import Flask, render_template, request, redirect, url_for, session, flash
import joblib
import numpy as np
import pandas as pd
import os
import requests
import re
from datetime import datetime
from supabase import create_client

app = Flask(__name__)
app.secret_key = 'mumbai_waterwatch_secret_key'

BASE = os.path.dirname(os.path.abspath(__file__))
model_clf = joblib.load(os.path.join(BASE, 'models', 'model_classifier.pkl'))
model_reg = joblib.load(os.path.join(BASE, 'models', 'model_regressor.pkl'))
model_arima = joblib.load(os.path.join(BASE, 'models', 'model_arima.pkl'))

df = pd.read_csv(os.path.join(BASE, 'mumbai_master_dataset.csv'))

SUPABASE_URL = os.environ.get('SUPABASE_URL', 'https://keslogxobehjuliuvrxk.supabase.co')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', 'sb_publishable_JTNmXo5RZQD4SU_Htyvg2Q_6BgzJE1v')
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

USERS = {
    'admin': 'waterwatch123',
    'aabid': 'mumbai2025'
}

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            flash('Please login first.')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

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
    session.pop('user', None)
    return redirect(url_for('login'))

@app.route('/')
@login_required
def home():
    # Get live reservoir % from Supabase
    try:
        response = supabase.table('lake_levels').select('*').execute()
        rows = response.data
        reservoir = rows[0]['combined_pct'] if rows else 83.09
    except:
        reservoir = 83.09

    # Rest from master dataset
    latest = df.iloc[-1]
    rainfall = round(latest['rainfall_mm'], 2)
    temp = round(latest['avg_temp'], 2)
    humidity = round(latest['avg_humidity'], 2)
    severity = latest['shortage_severity']
    consumption_mld = int(latest['consumption_mld'])

    return render_template('index.html',
        reservoir=reservoir,
        rainfall=rainfall,
        temp=temp,
        humidity=humidity,
        severity=severity,
        consumption_mld=consumption_mld,
        user=session['user'])

@app.route('/predict', methods=['GET', 'POST'])
@login_required
def predict():
    if request.method == 'POST':
        rainfall = float(request.form['rainfall'])
        temp = float(request.form['temp'])
        humidity = float(request.form['humidity'])
        reservoir = float(request.form['reservoir'])
        consumption = float(request.form['consumption'])
        month = int(request.form['month'])

        features = np.array([[rainfall, temp, humidity, reservoir, consumption]])
        features_reg = np.array([[rainfall, temp, humidity, consumption, month]])

        prediction = model_clf.predict(features)[0]
        predicted_reservoir = round(float(model_reg.predict(features_reg)[0]), 2)

        forecast = model_arima.predict(n_periods=12)
        reservoir_forecast = [round(min(100, max(0, f)), 2) for f in forecast]

        session['prediction'] = prediction
        session['predicted_reservoir'] = predicted_reservoir
        session['reservoir_forecast'] = reservoir_forecast
        session['form_data'] = {
            'rainfall': rainfall, 'temp': temp,
            'humidity': humidity, 'reservoir': reservoir,
            'consumption': consumption, 'month': month
        }
        return redirect(url_for('result'))

    return render_template('predict.html', user=session.get('user'))

@app.route('/result')
@login_required
def result():
    prediction = session.get('prediction')
    predicted_reservoir = session.get('predicted_reservoir')
    reservoir_forecast = session.get('reservoir_forecast')
    form_data = session.get('form_data', {})

    if not prediction:
        return redirect(url_for('predict'))

    return render_template('result.html',
        prediction=prediction,
        predicted_reservoir=predicted_reservoir,
        reservoir_forecast=reservoir_forecast,
        form_data=form_data,
        user=session.get('user'))

@app.route('/livelevels')
@login_required
def livelevels():
    lake_data = []
    combined = None
    last_updated = None
    error = None

    try:
        response = supabase.table('lake_levels').select('*').execute()
        rows = response.data

        lake_data = [{'name': r['lake_name'], 'pct': r['percentage']} for r in rows]
        combined = rows[0]['combined_pct'] if rows else None
        last_updated = rows[0]['last_updated'] if rows else None
        error = None

    except Exception as e:
        lake_data = []
        combined = None
        last_updated = None
        error = 'Could not fetch data from database.'

    return render_template('livelevels.html',
        lake_data=lake_data,
        combined=combined,
        last_updated=last_updated,
        error=error,
        user=session.get('user'))

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
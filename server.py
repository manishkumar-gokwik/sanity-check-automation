"""
Flask server — Sanity Check Automation Dashboard
Production-ready with all 5 checks.
"""

from flask import Flask, render_template, jsonify, request, send_file
import asyncio
import logging
import json
import os
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)s %(levelname)s %(message)s'
)

app = Flask(__name__)

# File-based progress tracking (works across gunicorn workers)
PROGRESS_FILE = '/tmp/sanity_progress.json'

def _read_progress():
    try:
        if os.path.exists(PROGRESS_FILE):
            with open(PROGRESS_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {"stage": "", "merchant": "", "merchant_idx": 0, "total": 0, "done": False}

def _write_progress(data):
    try:
        with open(PROGRESS_FILE, 'w') as f:
            json.dump(data, f)
    except Exception:
        pass

# Start scheduler
try:
    from modules.scheduler import start_scheduler
    start_scheduler()
except Exception as e:
    logging.warning(f"Scheduler not started: {e}")


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/sheets-status')
def sheets_status():
    try:
        from modules.sheets_reader import test_connection
        return jsonify(test_connection())
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/merchants')
def get_merchants():
    try:
        from modules.sheets_reader import get_sanity_sample
        df = get_sanity_sample()
        df = df[df['Merchant Name'].astype(str).str.strip() != '']
        return jsonify({"success": True, "merchants": df.fillna('').to_dict(orient='records'), "count": len(df)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/run-batch', methods=['POST'])
def run_batch():
    data = request.json or {}
    selected_date = data.get('date', '')
    try:
        # File-based progress — shared across workers
        progress = {"stage": "starting", "merchant": "", "merchant_idx": 0, "total": 0, "done": False}
        _write_progress(progress)

        # Wrap progress to auto-save on update
        class FileProgress(dict):
            def __setitem__(self, key, value):
                super().__setitem__(key, value)
                _write_progress(dict(self))

        file_progress = FileProgress(progress)
        from modules.sanity_engine import run_batch_sanity_check
        result = asyncio.run(run_batch_sanity_check(selected_date, progress=file_progress))
        file_progress['done'] = True
        return jsonify({"success": True, "result": result})
    except Exception as e:
        p = _read_progress()
        p['done'] = True
        _write_progress(p)
        logging.exception("Batch check failed")
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/progress')
def get_progress():
    return jsonify(_read_progress())


@app.route('/api/write-results', methods=['POST'])
def write_results():
    try:
        data = request.json
        results = data.get('results', [])
        from modules.sheets_writer import write_results as do_write
        return jsonify(do_write(results))
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/scheduler-status')
def scheduler_status():
    try:
        from modules.scheduler import get_status
        return jsonify(get_status())
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route('/api/run-now', methods=['POST'])
def run_now():
    try:
        from modules.scheduler import run_now
        return jsonify(run_now())
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route('/api/generate-email', methods=['POST'])
def generate_email():
    try:
        data = request.json
        results = data.get('results', [])
        from modules.email_drafter import generate_discrepancy_email
        email_data = generate_discrepancy_email(results)
        if email_data:
            return jsonify({"success": True, "email": email_data})
        return jsonify({"success": True, "email": None, "message": "No discrepancies found"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/download-report', methods=['POST'])
def download_report():
    try:
        data = request.json
        from modules.report_generator import generate_report
        path = generate_report(data)
        return send_file(path, as_attachment=True,
                        download_name=f'sanity_report_{datetime.now().strftime("%Y%m%d")}.pdf')
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


if __name__ == '__main__':
    app.run(debug=True, port=5000)

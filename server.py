"""
Flask server — Sanity Check Automation Dashboard
Production-ready with all 5 checks.
"""

from flask import Flask, render_template, jsonify, request, send_file
import asyncio
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)s %(levelname)s %(message)s'
)

app = Flask(__name__)

# Progress tracking for stage-wise updates
_progress = {"stage": "", "merchant": "", "merchant_idx": 0, "total": 0, "done": False}

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
        _progress['done'] = False
        _progress['stage'] = 'starting'
        from modules.sanity_engine import run_batch_sanity_check
        result = asyncio.run(run_batch_sanity_check(selected_date, progress=_progress))
        _progress['done'] = True
        return jsonify({"success": True, "result": result})
    except Exception as e:
        _progress['done'] = True
        logging.exception("Batch check failed")
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/progress')
def get_progress():
    return jsonify(_progress)


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

api: uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
worker: python -m app.worker
admin: streamlit run admin_panel/main.py --server.port ${PORT:-8501} --server.address 0.0.0.0

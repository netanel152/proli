#!/bin/bash

# הפעלת ה-Worker ברקע
python -m app.worker &
status=$?
if [ $status -ne 0 ]; then
  echo "Failed to start worker: $status"
  exit $status
fi

# הפעלת האדמין ברקע
streamlit run admin_panel/main.py --server.port 8501 --server.address 0.0.0.0 &
status=$?
if [ $status -ne 0 ]; then
  echo "Failed to start admin panel: $status"
  exit $status
fi

# הפעלת ה-API (בחזית - זה מה שמחזיק את הקונטיינר חי)
uvicorn app.main:app --host 0.0.0.0 --port 8000
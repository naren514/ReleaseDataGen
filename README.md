# 📦 ReleaseDataGen

ReleaseDataGen is a **Streamlit-based tool** for generating and posting **Oracle Transportation Management (OTM)** Order payloads.  
It supports:

- ✅ Sales Orders (Order Release payloads)  
- ✅ Purchase Orders (TransOrder payloads)  
- ✅ Manual entry OR CSV/Excel import  
- ✅ Randomized test data generation  
- ✅ Safe OTM posting (restricted to **dev/test endpoints only**)  

---

## 🚀 Features
- Generate valid OTM XML payloads for **Sales Orders** and **Purchase Orders**.  
- Import **CSV/Excel files** to create orders directly from structured data.  
- Download generated payloads as **XML** or in a single **ZIP**.  
- Post payloads directly to OTM via its integration endpoint.  
- Built-in guardrails:  
  - Block PROD endpoints (only allow URLs containing `dev` or `test`).  
  - Sequential Release Line ID generation (`SO_XXXX_001`, `SO_XXXX_002`, …).  
- Lightweight, runs anywhere (local machine, container, or Cloud Run).  

---

## 📸 Screenshots

> Replace these placeholders with actual screenshots or GIFs of your app.  
> Save images in an `assets/` folder and update paths below.

### 🔐 Login & Passcode
![Login Screenshot](assets/screenshot_login.png)

### 🎛️ Order Builder
![Order Builder Screenshot](assets/screenshot_builder.png)

### 📥 CSV/Excel Import
![CSV Import Screenshot](assets/screenshot_csv.png)

### 🗺️ Tracking (Optional Extension)
![Tracking Screenshot](assets/screenshot_tracking.png)

---

## 📂 Project Structure
```
ReleaseDataGen/
├── app.py             # Streamlit app
├── requirements.txt   # Python dependencies
├── Dockerfile         # Container build config
├── README.md          # Project documentation
└── assets/            # (add your screenshots here)
```

---

## 🖥️ Run Locally

### 1. Clone the repo
```bash
git clone https://github.com/naren514/ReleaseDataGen.git
cd ReleaseDataGen
```

### 2. Create & activate a virtual environment (recommended)
```bash
python3 -m venv .venv
source .venv/bin/activate   # macOS/Linux
.venv\Scripts\activate      # Windows
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Run Streamlit
```bash
streamlit run app.py
```

Open [http://localhost:8501](http://localhost:8501) in your browser.

---

## ☁️ Deploy to Google Cloud Run

### 1. Build the container
```bash
gcloud builds submit --tag gcr.io/<PROJECT_ID>/otm-release-tool
```

### 2. Deploy to Cloud Run
```bash
gcloud run deploy otm-release-tool   --image gcr.io/<PROJECT_ID>/otm-release-tool   --region <REGION>   --allow-unauthenticated   --set-env-vars APP_PASS=mysupersecret   --memory 512Mi
```

### 3. Access your app
Cloud Run will return a public URL (e.g. `https://otm-release-tool-xxxxx.a.run.app`).  
Log in with the passcode you set via `APP_PASS`.

---

## 📑 CSV/Excel Templates

### Sales Orders
```csv
order_id,ship_from_xid,ship_to_xid,item_xid,qty,value,currency
SO_09000-1128,110,10000000000013,400000002438186,1900,9720,USD
SO_09000-1128,110,10000000000013,300000005438196,1900,9720,USD
```

### Purchase Orders
```csv
po_xid,supplier_ship_from_xid,dc_ship_to_xid,packaged_item_xid,qty,declared_value,item_number,line_number,schedule_number,currency,early_pickup_dt,late_pickup_dt,tz_id,tz_offset,plan_from_location_xid
PO_09000-1128,300000016179177,110,400000004438186,2800,9702,116783,1,1,USD,20250718102700,20250725102700,Asia/Taipei,+08:00,CNNGB
```

---

## 🔐 Security Notes
- The app **blocks posting** to any OTM endpoint that does not contain `dev` or `test` in the URL.  
- Do **not** store production credentials in the app.  
- Use `APP_PASS` env variable to protect access.  

---

## 🤝 Contributing
Pull requests are welcome! For major changes, open an issue first to discuss what you’d like to change.

---

## 📜 License
MIT License © 2025 naren514

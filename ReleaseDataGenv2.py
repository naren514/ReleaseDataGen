import os
import io
import time
import random
import datetime
import zipfile
import gzip
import requests
import xml.etree.ElementTree as ET
import pandas as pd
import streamlit as st

# ===== must be first Streamlit call =====
st.set_page_config(page_title="OTM Order Generator (SO/PO + CSV/XLSX)", page_icon="📦", layout="wide")

# =========================
# 🔐 Passcode Gate (env: APP_PASS)
# =========================
APP_PASS = os.getenv("APP_PASS", "")
if APP_PASS:
    with st.sidebar:
        token = st.text_input("Enter app passcode", type="password", placeholder="••••••••")
    if token != APP_PASS:
        st.warning("Please enter the correct passcode to use the app.")
        st.stop()

# =========================
# 🧰 Helpers
# =========================
def make_glog_date(dt: datetime.datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S")

def _parse_list(s: str):
    return [p.strip() for p in s.replace(",", "\n").split("\n") if p.strip()]

def is_nonprod_url(url: str) -> bool:
    """Allow only URLs that clearly target non-prod (must contain 'dev' or 'test')."""
    if not url:
        return False
    u = url.lower()
    return ("dev" in u) or ("test" in u)

# =========================
# 🧰 Sales Order XML (Release)
# =========================
def build_release_xml(
    *,
    domain: str,
    base_release_xid: str,
    ship_from_xid: str,
    ship_to_xid: str,
    lines: list,                                  # [{"item_xid","qty","value", ("currency" optional), ("line_xid" optional)}]
    release_index: int = 1,
    use_release_suffix_in_gid: bool = False,      # False => ReleaseGid = base_release_xid
    use_release_suffix_in_line_ids: bool = False, # False => line prefix = base_release_xid
    currency: str = "USD",                        # default if line-level currency not provided
) -> bytes:
    """
    Builds a <otm:Release> payload.
    - ReleaseGid: base or base_R#
    - ReleaseLineGid: if line['line_xid'] provided, use it; else prefix (base or base_R#) + _001, _002, ...
    - Each line can override currency via line["currency"]
    """
    now = datetime.datetime.utcnow()
    early = now + datetime.timedelta(days=7)
    late  = early + datetime.timedelta(days=1)

    otm_ns = "http://xmlns.oracle.com/apps/otm/transmission/v6.4"
    gtm_ns = "http://xmlns.oracle.com/apps/gtm/transmission/v6.4"
    E = lambda tag: f"{{{otm_ns}}}{tag}"

    release_suffix = f"R{release_index}"
    release_gid_xid = f"{base_release_xid}_{release_suffix}" if use_release_suffix_in_gid else base_release_xid
    line_prefix = f"{base_release_xid}_{release_suffix}" if use_release_suffix_in_line_ids else base_release_xid

    root = ET.Element(f"{{{otm_ns}}}Transmission", {"xmlns:otm": otm_ns, "xmlns:gtm": gtm_ns})
    th = ET.SubElement(root, E("TransmissionHeader"))
    tcd = ET.SubElement(th, E("TransmissionCreateDt"))
    ET.SubElement(tcd, E("GLogDate")).text = make_glog_date(now)

    body = ET.SubElement(root, E("TransmissionBody"))
    glx = ET.SubElement(body, E("GLogXMLElement"))
    rel = ET.SubElement(glx, E("Release"))

    # Release GID
    rgid = ET.SubElement(rel, E("ReleaseGid"))
    gid = ET.SubElement(rgid, E("Gid"))
    ET.SubElement(gid, E("DomainName")).text = domain
    ET.SubElement(gid, E("Xid")).text = release_gid_xid

    ET.SubElement(rel, E("TransactionCode")).text = "IU"

    # ShipFrom
    sfrom = ET.SubElement(rel, E("ShipFromLocationRef"))
    lref = ET.SubElement(sfrom, E("LocationRef"))
    lgid = ET.SubElement(lref, E("LocationGid"))
    gid3 = ET.SubElement(lgid, E("Gid"))
    ET.SubElement(gid3, E("DomainName")).text = domain
    ET.SubElement(gid3, E("Xid")).text = ship_from_xid

    # ShipTo
    sto = ET.SubElement(rel, E("ShipToLocationRef"))
    lref2 = ET.SubElement(sto, E("LocationRef"))
    lgid2 = ET.SubElement(lref2, E("LocationGid"))
    gid4 = ET.SubElement(lgid2, E("Gid"))
    ET.SubElement(gid4, E("DomainName")).text = domain
    ET.SubElement(gid4, E("Xid")).text = ship_to_xid

    # TimeWindow (simple default)
    tw = ET.SubElement(rel, E("TimeWindow"))
    ep = ET.SubElement(tw, E("EarlyPickupDt"))
    ET.SubElement(ep, E("GLogDate")).text = make_glog_date(early)
    lp = ET.SubElement(tw, E("LatePickupDt"))
    ET.SubElement(lp, E("GLogDate")).text = make_glog_date(late)

    # Lines (sequential or user-specified)
    for idx, line in enumerate(lines, start=1):
        default_line_xid = f"{line_prefix}_{idx:03d}"
        line_xid = str(line.get("line_xid", "")).strip() or default_line_xid

        rl = ET.SubElement(rel, E("ReleaseLine"))
        rlg = ET.SubElement(rl, E("ReleaseLineGid"))
        gidL = ET.SubElement(rlg, E("Gid"))
        ET.SubElement(gidL, E("DomainName")).text = domain
        ET.SubElement(gidL, E("Xid")).text = line_xid

        ET.SubElement(rl, E("TransactionCode")).text = "IU"

        # Item
        piref = ET.SubElement(rl, E("PackagedItemRef"))
        pig = ET.SubElement(piref, E("PackagedItemGid"))
        gidP = ET.SubElement(pig, E("Gid"))
        ET.SubElement(gidP, E("DomainName")).text = domain
        ET.SubElement(gidP, E("Xid")).text = line["item_xid"]

        # Quantity + Declared Value (per-line currency if provided)
        iq = ET.SubElement(rl, E("ItemQuantity"))
        ET.SubElement(iq, E("PackagedItemCount")).text = str(int(line["qty"]))
        dv = ET.SubElement(iq, E("DeclaredValue"))
        fa = ET.SubElement(dv, E("FinancialAmount"))
        ET.SubElement(fa, E("GlobalCurrencyCode")).text = str(line.get("currency", currency))
        ET.SubElement(fa, E("MonetaryAmount")).text = str(float(line["value"]))

    # ReleaseType + Refnums (Sales Order defaults)
    rtg = ET.SubElement(rel, E("ReleaseTypeGid"))
    gidT = ET.SubElement(rtg, E("Gid"))
    ET.SubElement(gidT, E("Xid")).text = "SALES_ORDER"

    rref1 = ET.SubElement(rel, E("ReleaseRefnum"))
    rrq1 = ET.SubElement(rref1, E("ReleaseRefnumQualifierGid"))
    gidQ1 = ET.SubElement(rrq1, E("Gid"))
    ET.SubElement(gidQ1, E("DomainName")).text = domain
    ET.SubElement(gidQ1, E("Xid")).text = "ORDER_TYPE"
    ET.SubElement(rref1, E("ReleaseRefnumValue")).text = "SALES_ORDER"

    rref2 = ET.SubElement(rel, E("ReleaseRefnum"))
    rrq2 = ET.SubElement(rref2, E("ReleaseRefnumQualifierGid"))
    gidQ2 = ET.SubElement(rrq2, E("Gid"))
    ET.SubElement(gidQ2, E("DomainName")).text = domain
    ET.SubElement(gidQ2, E("Xid")).text = "DIRECTION"
    ET.SubElement(rref2, E("ReleaseRefnumValue")).text = "OUTBOUND"

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)

# =========================
# 🧰 Purchase Order XML (TransOrder)
# =========================
def build_purchase_order_xml(
    *,
    domain: str = "THG",
    po_xid: str = "PO_09000-1128",
    # Header basics
    release_method_xid: str = "AUTO_CALC - THG",
    supplier_ship_from_xid: str = "300000016179177",   # Supplier location (SHIP FROM)
    dc_ship_to_xid: str = "110",                       # Your DC (SHIP TO)
    # Header refnums (optional overrides)
    supplier_id: str = "10010",
    supplier_name: str = "BPT - PRO POWER CO LTD",
    le_name: str = "THE HILLMAN GROUP",
    buyer: str = "THE HILLMAN GROUP",
    supplier_site_name: str = "KAOHSIUNG CITY",
    revision_num: str = "0",
    # Flex fields on header
    ff_attr2_text: str = "SHIP METHOD",
    ff_attr3_text: str = "Y",
    ff_attr4_text: str = "FREIGHT TERMS",
    ff_number1: str = "100000019476400",
    ff_date1_yyyymmddhhmmss: str = "20250925000000",
    # Lines: list of dicts (see below)
    lines: list = None,
    # Currency defaults
    currency: str = "USD",
    rate_to_base: float = 1.0,
    func_currency_amount: float = 0.0,
    # Time window / TZ for lines
    early_pickup_dt: str = "20250718102700",
    late_pickup_dt: str  = "20250725102700",
    tz_id: str = "Asia/Taipei",
    tz_offset: str = "+08:00",
    # Optional planning origin
    plan_from_location_xid: str = "CNNGB",
) -> bytes:
    """
    Builds a <otm:TransOrder> (Purchase Order) payload.

    Each element in `lines`:
      {
        "packaged_item_xid": "...",
        "qty": 2800,
        "declared_value": 9702.0,
        "item_number": "116783",         # optional
        "line_number": 1,                # default sequential if omitted
        "schedule_number": 1,            # default 1
        "currency": "USD"                # optional per-line currency
      }

    TransOrderLineGid = f"{po_xid}-{line_number:03d}-{schedule_number:03d}"
    """
    if lines is None:
        lines = []

    otm_ns = "http://xmlns.oracle.com/apps/otm/transmission/v6.4"
    gtm_ns = "http://xmlns.oracle.com/apps/gtm/transmission/v6.4"
    E = lambda tag: f"{{{otm_ns}}}{tag}"

    root = ET.Element(f"{{{otm_ns}}}Transmission", {"xmlns:otm": otm_ns, "xmlns:gtm": gtm_ns})
    ET.SubElement(root, E("TransmissionHeader"))

    body = ET.SubElement(root, E("TransmissionBody"))
    glx = ET.SubElement(body, E("GLogXMLElement"))
    to = ET.SubElement(glx, E("TransOrder"))

    # Header
    toh = ET.SubElement(to, E("TransOrderHeader"))

    tog = ET.SubElement(toh, E("TransOrderGid"))
    gid = ET.SubElement(tog, E("Gid"))
    ET.SubElement(gid, E("DomainName")).text = domain
    ET.SubElement(gid, E("Xid")).text = po_xid

    ET.SubElement(toh, E("TransactionCode")).text = "IU"

    # Release method
    rmg = ET.SubElement(toh, E("ReleaseMethodGid"))
    gid_rm = ET.SubElement(rmg, E("Gid"))
    ET.SubElement(gid_rm, E("DomainName")).text = domain
    ET.SubElement(gid_rm, E("Xid")).text = release_method_xid

    # InvolvedParty SHIP FROM
    ip = ET.SubElement(toh, E("InvolvedParty"))
    ipq = ET.SubElement(ip, E("InvolvedPartyQualifierGid"))
    gid_ipq = ET.SubElement(ipq, E("Gid"))
    ET.SubElement(gid_ipq, E("Xid")).text = "SHIP FROM"

    ip_loc_ref = ET.SubElement(ip, E("InvolvedPartyLocationRef"))
    loc_ref = ET.SubElement(ip_loc_ref, E("LocationRef"))
    loc_gid = ET.SubElement(loc_ref, E("LocationGid"))
    gid_loc = ET.SubElement(loc_gid, E("Gid"))
    ET.SubElement(gid_loc, E("DomainName")).text = domain
    ET.SubElement(gid_loc, E("Xid")).text = supplier_ship_from_xid

    contact_ref = ET.SubElement(ip, E("ContactRef"))
    contact = ET.SubElement(contact_ref, E("Contact"))
    contact_gid = ET.SubElement(contact, E("ContactGid"))
    gid_c = ET.SubElement(contact_gid, E("Gid"))
    ET.SubElement(gid_c, E("DomainName")).text = domain
    ET.SubElement(gid_c, E("Xid")).text = supplier_ship_from_xid

    # OrderType = PURCHASE_ORDER
    otg = ET.SubElement(toh, E("OrderTypeGid"))
    gid_ot = ET.SubElement(otg, E("Gid"))
    ET.SubElement(gid_ot, E("Xid")).text = "PURCHASE_ORDER"

    # OrderRefnums
    def add_order_refnum(qual_xid: str, value: str):
        rn = ET.SubElement(toh, E("OrderRefnum"))
        rq = ET.SubElement(rn, E("OrderRefnumQualifierGid"))
        gid_rq = ET.SubElement(rq, E("Gid"))
        ET.SubElement(gid_rq, E("DomainName")).text = domain
        ET.SubElement(gid_rq, E("Xid")).text = qual_xid
        ET.SubElement(rn, E("OrderRefnumValue")).text = value

    add_order_refnum("SUPPLIER_ID", supplier_id)
    add_order_refnum("SUPPLIER_NAME", supplier_name)
    add_order_refnum("LE_NAME", le_name)
    add_order_refnum("BUYER", buyer)
    add_order_refnum("SUPPLIER_SITE_NAME", supplier_site_name)
    add_order_refnum("REVISION_NUM", revision_num)

    # Flex fields (Strings/Numbers/Dates)
    ffs = ET.SubElement(toh, E("FlexFieldStrings"))
    ET.SubElement(ffs, E("Attribute2")).text = ff_attr2_text
    ET.SubElement(ffs, E("Attribute3")).text = ff_attr3_text
    ET.SubElement(ffs, E("Attribute4")).text = ff_attr4_text

    ffn = ET.SubElement(toh, E("FlexFieldNumbers"))
    ET.SubElement(ffn, E("AttributeNumber1")).text = str(ff_number1)

    ffd = ET.SubElement(toh, E("FlexFieldDates"))
    ad1 = ET.SubElement(ffd, E("AttributeDate1"))
    ET.SubElement(ad1, E("GLogDate")).text = ff_date1_yyyymmddhhmmss

    ET.SubElement(toh, E("FlexFieldCurrencies"))

    # Lines
    told = ET.SubElement(to, E("TransOrderLineDetail"))

    for idx, L in enumerate(lines, start=1):
        line_number = int(L.get("line_number", idx))
        schedule_number = int(L.get("schedule_number", 1))
        packaged_item_xid = L["packaged_item_xid"]
        qty = int(L["qty"])
        declared_value = float(L["declared_value"])
        item_number = str(L.get("item_number", ""))
        line_currency = str(L.get("currency", currency))

        tol = ET.SubElement(told, E("TransOrderLine"))

        # TransOrderLineGid: POXID-<line>-<schedule> (3-digit pads)
        tolg = ET.SubElement(tol, E("TransOrderLineGid"))
        gid_l = ET.SubElement(tolg, E("Gid"))
        ET.SubElement(gid_l, E("DomainName")).text = domain
        ET.SubElement(gid_l, E("Xid")).text = f"{po_xid}-{line_number:03d}-{schedule_number:03d}"

        ET.SubElement(tol, E("TransactionCode")).text = "IU"

        # Item
        piref = ET.SubElement(tol, E("PackagedItemRef"))
        pig = ET.SubElement(piref, E("PackagedItemGid"))
        gid_pi = ET.SubElement(pig, E("Gid"))
        ET.SubElement(gid_pi, E("DomainName")).text = domain
        ET.SubElement(gid_pi, E("Xid")).text = packaged_item_xid

        # ShipFrom / ShipTo (per line)
        sfrom = ET.SubElement(tol, E("ShipFromLocationRef"))
        lref_from = ET.SubElement(sfrom, E("LocationRef"))
        lgid_from = ET.SubElement(lref_from, E("LocationGid"))
        gid_from = ET.SubElement(lgid_from, E("Gid"))
        ET.SubElement(gid_from, E("DomainName")).text = domain
        ET.SubElement(gid_from, E("Xid")).text = supplier_ship_from_xid

        sto = ET.SubElement(tol, E("ShipToLocationRef"))
        lref_to = ET.SubElement(sto, E("LocationRef"))
        lgid_to = ET.SubElement(lref_to, E("LocationGid"))
        gid_to = ET.SubElement(lgid_to, E("Gid"))
        ET.SubElement(gid_to, E("DomainName")).text = domain
        ET.SubElement(gid_to, E("Xid")).text = dc_ship_to_xid

        # Quantity + Declared Value (+ currency details)
        iq = ET.SubElement(tol, E("ItemQuantity"))
        ET.SubElement(iq, E("PackagedItemCount")).text = str(qty)
        dv = ET.SubElement(iq, E("DeclaredValue"))
        fa = ET.SubElement(dv, E("FinancialAmount"))
        ET.SubElement(fa, E("GlobalCurrencyCode")).text = line_currency
        ET.SubElement(fa, E("MonetaryAmount")).text = str(declared_value)
        ET.SubElement(fa, E("RateToBase")).text = str(rate_to_base)
        ET.SubElement(fa, E("FuncCurrencyAmount")).text = str(func_currency_amount)

        # TimeWindow with TZ info
        tw = ET.SubElement(tol, E("TimeWindow"))
        ep = ET.SubElement(tw, E("EarlyPickupDt"))
        ET.SubElement(ep, E("GLogDate")).text = early_pickup_dt
        ET.SubElement(ep, E("TZId")).text = tz_id
        ET.SubElement(ep, E("TZOffset")).text = tz_offset

        lp = ET.SubElement(tw, E("LatePickupDt"))
        ET.SubElement(lp, E("GLogDate")).text = late_pickup_dt
        ET.SubElement(lp, E("TZId")).text = tz_id
        ET.SubElement(lp, E("TZOffset")).text = tz_offset

        # PlanFromLocationGid
        pfg = ET.SubElement(tol, E("PlanFromLocationGid"))
        pfg_loc = ET.SubElement(pfg, E("LocationGid"))
        gid_pf = ET.SubElement(pfg_loc, E("Gid"))
        ET.SubElement(gid_pf, E("DomainName")).text = domain
        ET.SubElement(gid_pf, E("Xid")).text = plan_from_location_xid

        # OrderLine refnums
        def add_line_refnum(qual_xid: str, value: str):
            oln = ET.SubElement(tol, E("OrderLineRefnum"))
            olq = ET.SubElement(oln, E("OrderLineRefnumQualifierGid"))
            gid_olq = ET.SubElement(olq, E("Gid"))
            ET.SubElement(gid_olq, E("DomainName")).text = domain
            ET.SubElement(gid_olq, E("Xid")).text = qual_xid
            ET.SubElement(oln, E("OrderLineRefnumValue")).text = value

        add_line_refnum("LINE_NUMBER", str(line_number))
        add_line_refnum("SCHEDULE_NUMBER", str(schedule_number))
        if item_number:
            add_line_refnum("ITEM_NUMBER", item_number)

        # Line Flex fields (Strings/Numbers)
        lffs = ET.SubElement(tol, E("FlexFieldStrings"))
        ET.SubElement(lffs, E("Attribute1")).text = "COUNTRY_OF_ORIGIN"
        ET.SubElement(lffs, E("Attribute2")).text = "UOMCODE"

        lffn = ET.SubElement(tol, E("FlexFieldNumbers"))
        ET.SubElement(lffn, E("AttributeNumber1")).text = str(ff_number1)
        ET.SubElement(lffn, E("AttributeNumber2")).text = str(ff_number1)

        ET.SubElement(tol, E("FlexFieldDates"))  # empty block

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)

# =========================
# 🌐 POST + ACK helpers
# =========================
def post_to_otm(otm_url: str, username: str, password: str, xml_bytes: bytes, gzip_payload: bool=False) -> str:
    headers = {"Content-Type": "text/xml; charset=UTF-8"}
    data = gzip.compress(xml_bytes) if gzip_payload else xml_bytes
    if gzip_payload:
        headers["Content-Encoding"] = "gzip"
    resp = requests.post(otm_url, auth=(username, password), data=data, headers=headers, timeout=60)
    resp.raise_for_status()
    return resp.text

def parse_ack_for_status(xml_text: str):
    try:
        root = ET.fromstring(xml_text)
        txt = ET.tostring(root, encoding="unicode")
        if "SEVERITY_ERROR" in txt or "ERROR" in txt:
            return ("ERROR", txt[:1000])
        if "SEVERITY_WARNING" in txt or "WARNING" in txt:
            return ("WARNING", txt[:1000])
        return ("OK", txt[:1000])
    except ET.ParseError:
        return ("UNKNOWN", xml_text[:1000])

# =========================
# 📥 Templates + Import Core (CSV / Excel)
# =========================
# Added columns for SO: release_line_id and line_number (both optional; either can drive the line XID)
SO_CSV_TEMPLATE = """order_id,ship_from_xid,ship_to_xid,item_xid,qty,value,currency,release_line_id,line_number
SO_09000-1128,110,10000000000013,400000002438186,1900,9720,USD,SO_09000-1128_001,1
SO_09000-1128,110,10000000000013,300000005438196,1900,9720,USD,SO_09000-1128_002,2
"""

PO_CSV_TEMPLATE = """po_xid,supplier_ship_from_xid,dc_ship_to_xid,packaged_item_xid,qty,declared_value,item_number,line_number,schedule_number,currency,early_pickup_dt,late_pickup_dt,tz_id,tz_offset,plan_from_location_xid,supplier_id,supplier_name,le_name,buyer,supplier_site_name,revision_num
PO_09000-1128,300000016179177,110,400000004438186,2800,9702,116783,1,1,USD,20250718102700,20250725102700,Asia/Taipei,+08:00,CNNGB,10010,BPT - PRO POWER CO LTD,THE HILLMAN GROUP,THE HILLMAN GROUP,KAOHSIUNG CITY,0
"""

def _download_template_csv(name: str, content: str):
    st.download_button(
        f"⬇️ Download {name} CSV template",
        data=content.encode("utf-8"),
        file_name=f"{name.lower().replace(' ','_')}_template.csv",
        mime="text/csv",
        use_container_width=True
    )

def _read_tabular(uploaded):
    """Read CSV/Excel into DataFrame. Requires openpyxl for .xlsx/.xls."""
    name = (uploaded.name or "").lower()
    try:
        if name.endswith(".csv"):
            return pd.read_csv(uploaded)
        elif name.endswith(".xlsx") or name.endswith(".xls"):
            return pd.read_excel(uploaded)  # engine auto; ensure openpyxl in requirements
        else:
            try:
                uploaded.seek(0)
                return pd.read_csv(uploaded)
            except Exception:
                uploaded.seek(0)
                return pd.read_excel(uploaded)
    finally:
        try:
            uploaded.seek(0)
        except Exception:
            pass

def build_payloads_from_table(
    df: pd.DataFrame,
    order_kind: str,                  # "Sales Orders" or "Purchase Orders"
    *,
    domain: str,
    default_currency: str = "USD",
    # SO options:
    use_release_suffix_in_gid: bool = False,
    use_release_suffix_in_line_ids: bool = False,
):
    """
    Returns: List[ (human_id, ship_from, ship_to, lines_used, xml_bytes) ]

    SALES ORDERS required:
      order_id, ship_from_xid, ship_to_xid, item_xid, qty, value
    Optional: currency, release_line_id, line_number

    PURCHASE ORDERS required:
      po_xid, supplier_ship_from_xid, dc_ship_to_xid, packaged_item_xid, qty, declared_value
    Optional: item_number, line_number, schedule_number, currency,
              early_pickup_dt, late_pickup_dt, tz_id, tz_offset, plan_from_location_xid,
              supplier_id, supplier_name, le_name, buyer, supplier_site_name, revision_num
    """
    out = []
    cols = {c.lower(): c for c in df.columns}

    if order_kind == "Sales Orders":
        required = {"order_id", "ship_from_xid", "ship_to_xid", "item_xid", "qty", "value"}
        missing = required - set(cols.keys())
        if missing:
            raise ValueError(f"Missing required SO columns: {', '.join(sorted(missing))}")

        grouped = df.groupby([cols["order_id"], cols["ship_from_xid"], cols["ship_to_xid"]], dropna=False, sort=False)

        for (order_id, ship_from_xid, ship_to_xid), g in grouped:
            # preserve file order; compute line_xid using release_line_id or line_number; else auto by row order
            lines = []
            for row_idx, row in g.reset_index(drop=True).iterrows():
                line_currency = str(row[cols["currency"]]).strip() if "currency" in cols and pd.notna(row[cols["currency"]]) else default_currency

                explicit_line_id = ""
                if "release_line_id" in cols and pd.notna(row[cols["release_line_id"]]):
                    explicit_line_id = str(row[cols["release_line_id"]]).strip()

                line_num = None
                if "line_number" in cols and pd.notna(row[cols["line_number"]]):
                    try:
                        line_num = int(row[cols["line_number"]])
                    except Exception:
                        line_num = None

                # choose line_xid
                if explicit_line_id:
                    line_xid = explicit_line_id
                elif line_num is not None:
                    line_xid = f"{str(order_id).strip()}_{line_num:03d}"
                else:
                    # fallback to row order (1-based)
                    line_xid = f"{str(order_id).strip()}_{(row_idx+1):03d}"

                lines.append({
                    "item_xid": str(row[cols["item_xid"]]).strip(),
                    "qty": int(row[cols["qty"]]),
                    "value": float(row[cols["value"]]),
                    "currency": line_currency,
                    "line_xid": line_xid,
                })

            xml_bytes = build_release_xml(
                domain=domain,
                base_release_xid=str(order_id).strip(),
                ship_from_xid=str(ship_from_xid).strip(),
                ship_to_xid=str(ship_to_xid).strip(),
                lines=lines,
                release_index=1,  # CSV/XLSX import keeps R1 unless you wish to split further
                use_release_suffix_in_gid=use_release_suffix_in_gid,
                use_release_suffix_in_line_ids=use_release_suffix_in_line_ids,
                currency=default_currency,
            )
            human_id = f"{order_id}_R1" if use_release_suffix_in_gid else str(order_id).strip()
            out.append((human_id, str(ship_from_xid).strip(), str(ship_to_xid).strip(), lines, xml_bytes))

    else:
        required = {"po_xid", "supplier_ship_from_xid", "dc_ship_to_xid", "packaged_item_xid", "qty", "declared_value"}
        missing = required - set(cols.keys())
        if missing:
            raise ValueError(f"Missing required PO columns: {', '.join(sorted(missing))}")

        grouped = df.groupby([cols["po_xid"], cols["supplier_ship_from_xid"], cols["dc_ship_to_xid"]], dropna=False, sort=False)

        for (po_xid, supplier_ship_from_xid, dc_ship_to_xid), g in grouped:
            po_lines = []
            for _, row in g.iterrows():
                po_lines.append({
                    "packaged_item_xid": str(row[cols["packaged_item_xid"]]).strip(),
                    "qty": int(row[cols["qty"]]),
                    "declared_value": float(row[cols["declared_value"]]),
                    "item_number": str(row[cols["item_number"]]).strip() if "item_number" in cols and pd.notna(row[cols["item_number"]]) else "",
                    "line_number": int(row[cols["line_number"]]) if "line_number" in cols and pd.notna(row[cols["line_number"]]) else len(po_lines) + 1,
                    "schedule_number": int(row[cols["schedule_number"]]) if "schedule_number" in cols and pd.notna(row[cols["schedule_number"]]) else 1,
                    "currency": str(row[cols["currency"]]).strip() if "currency" in cols and pd.notna(row[cols["currency"]]) else default_currency,
                })

            # Optional header overrides if present
            hdr = lambda key, default: str(g.iloc[0][cols[key]]).strip() if key in cols and pd.notna(g.iloc[0][cols[key]]) else default
            early_pickup_dt = hdr("early_pickup_dt", "20250718102700")
            late_pickup_dt  = hdr("late_pickup_dt",  "20250725102700")
            tz_id           = hdr("tz_id",           "Asia/Taipei")
            tz_offset       = hdr("tz_offset",       "+08:00")
            plan_from       = hdr("plan_from_location_xid", "CNNGB")
            supplier_id     = hdr("supplier_id",     "10010")
            supplier_name   = hdr("supplier_name",   "BPT - PRO POWER CO LTD")
            le_name         = hdr("le_name",         "THE HILLMAN GROUP")
            buyer           = hdr("buyer",           "THE HILLMAN GROUP")
            supplier_site   = hdr("supplier_site_name", "KAOHSIUNG CITY")
            revision_num    = hdr("revision_num",    "0")

            xml_bytes = build_purchase_order_xml(
                domain=domain,
                po_xid=str(po_xid).strip(),
                release_method_xid=f"AUTO_CALC - {domain}",
                supplier_ship_from_xid=str(supplier_ship_from_xid).strip(),
                dc_ship_to_xid=str(dc_ship_to_xid).strip(),
                supplier_id=supplier_id,
                supplier_name=supplier_name,
                le_name=le_name,
                buyer=buyer,
                supplier_site_name=supplier_site,
                revision_num=revision_num,
                lines=po_lines,
                currency=default_currency,
                rate_to_base=1.0,
                func_currency_amount=0.0,
                early_pickup_dt=early_pickup_dt,
                late_pickup_dt=late_pickup_dt,
                tz_id=tz_id,
                tz_offset=tz_offset,
                plan_from_location_xid=plan_from,
            )
            human_id = str(po_xid).strip()
            out.append((human_id, str(supplier_ship_from_xid).strip(), str(dc_ship_to_xid).strip(), po_lines, xml_bytes))

    return out

# =========================
# 🧠 Session defaults
# =========================
def init_session_defaults():
    st.session_state.setdefault("remember_session", False)
    st.session_state.setdefault("otm_url", "")
    st.session_state.setdefault("otm_user", "")
    st.session_state.setdefault("otm_pass", "")

def clear_saved_creds():
    st.session_state["otm_url"] = ""
    st.session_state["otm_user"] = ""
    st.session_state["otm_pass"] = ""
    st.session_state["remember_session"] = False

init_session_defaults()

# =========================
# 🎛️ UI
# =========================
st.title("📦 OTM Order Generator")

order_kind = st.radio("What do you want to create?", ["Sales Orders", "Purchase Orders"], index=0, horizontal=True)
input_mode = st.radio("Input Mode", ["Manual (builder)", "Import (CSV/Excel)"], index=0, horizontal=True)

with st.sidebar:
    st.header("🔧 OTM Connection")
    remember = st.checkbox("Remember for this session", value=st.session_state["remember_session"])
    st.session_state["remember_session"] = remember

    otm_url = st.text_input(
        "OTM Endpoint (must contain 'dev' or 'test')",
        value=st.session_state["otm_url"] if remember else "",
        placeholder="https://<pod>-dev.gc3.oraclecloud.com/GC3/glog.integration.servlet.WMServlet",
    )
    otm_user = st.text_input(
        "OTM Username",
        value=st.session_state["otm_user"] if remember else "",
        placeholder="integration_user",
    )
    otm_pass = st.text_input(
        "OTM Password",
        value=st.session_state["otm_pass"] if remember else "",
        type="password",
        placeholder="••••••••",
    )

    if remember:
        st.session_state["otm_url"]  = otm_url
        st.session_state["otm_user"] = otm_user
        st.session_state["otm_pass"] = otm_pass

    if otm_url and not is_nonprod_url(otm_url):
        st.error("POSTs are disabled: OTM Endpoint must contain 'dev' or 'test'.")
    elif otm_url:
        st.success("Non-prod endpoint detected.")

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("Clear saved"):
            clear_saved_creds()
            st.success("Session credentials cleared.")
    with col_b:
        dry_run = st.checkbox("Dry run (don’t POST)", value=True)

# ===== Import Mode (CSV/XLSX) =====
if input_mode == "Import (CSV/Excel)":
    st.subheader("📥 Import Orders from CSV/Excel")
    st.caption("Upload CSV or Excel. For Sales Orders, you may include 'release_line_id' or 'line_number' to control the ReleaseLineGid; otherwise lines are auto-sequenced.")

    col_t1, col_t2 = st.columns(2)
    with col_t1:
        _download_template_csv("Sales Orders", SO_CSV_TEMPLATE)
    with col_t2:
        _download_template_csv("Purchase Orders", PO_CSV_TEMPLATE)

    uploaded = st.file_uploader("Upload CSV or Excel", type=["csv", "xlsx", "xls"], accept_multiple_files=False)

    domain = st.text_input("DomainName", value="THG")
    default_currency = st.text_input("Default Currency (used if not present on a line)", value="USD")

    if order_kind == "Sales Orders":
        use_release_suffix_in_gid = st.checkbox("Add release suffix (_R1) to Release GID (import)", value=False)
        use_release_suffix_in_line_ids = st.checkbox("Add release suffix (_R1) to SO LINE IDs (import)", value=False)
    else:
        use_release_suffix_in_gid = False
        use_release_suffix_in_line_ids = False

    col_run1, col_run2 = st.columns(2)
    generate_btn = col_run1.button("Generate from file")
    post_btn = col_run2.button("Generate & POST from file")

    if generate_btn or post_btn:
        if not uploaded:
            st.error("Please upload a CSV or Excel file.")
            st.stop()
        try:
            df = _read_tabular(uploaded)
        except Exception as e:
            st.error(f"Failed to read file: {e}")
            st.stop()

        try:
            payloads = build_payloads_from_table(
                df,
                order_kind,
                domain=domain,
                default_currency=default_currency,
                use_release_suffix_in_gid=use_release_suffix_in_gid,
                use_release_suffix_in_line_ids=use_release_suffix_in_line_ids,
            )
        except Exception as e:
            st.error(f"Validation/build error: {e}")
            st.stop()

        rows = []
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for human_id, ship_from, ship_to, lines_used, xml_bytes in payloads:
                status, snippet = ("NOT_POSTED", "(dry run)")
                if post_btn and not dry_run:
                    if not (otm_url and otm_user and otm_pass):
                        status, snippet = ("NO_CREDS", "Provide OTM Endpoint/User/Pass or enable Dry run.")
                    elif not is_nonprod_url(otm_url):
                        status, snippet = ("BLOCKED", "Endpoint must contain 'dev' or 'test'.")
                    else:
                        try:
                            ack = post_to_otm(otm_url, otm_user, otm_pass, xml_bytes, gzip_payload=False)
                            status, snippet = parse_ack_for_status(ack)
                        except requests.HTTPError as e:
                            resp = e.response
                            body = ""
                            try:
                                body = resp.text[:1000] if resp is not None else ""
                            except Exception:
                                pass
                            status = f"HTTP_ERROR {getattr(resp, 'status_code', '')}"
                            snippet = f"{e} :: {body}"
                        except requests.RequestException as e:
                            status = "NETWORK_ERROR"
                            snippet = str(e)[:1000]
                        except Exception as e:
                            status = "APP_ERROR"
                            snippet = str(e)[:1000]

                rows.append({
                    "Order Kind": "SO" if order_kind == "Sales Orders" else "PO",
                    "Order ID": human_id,
                    "Ship From": ship_from,
                    "Ship To": ship_to,
                    "# Lines": len(lines_used),
                    "Status": status,
                    "Ack / Note": snippet
                })
                zf.writestr(f"{human_id}.xml", xml_bytes)

        zip_buf.seek(0)
        st.success(f"Built {len(payloads)} order(s) from file.")
        st.dataframe(rows, use_container_width=True)
        st.download_button(
            "⬇️ Download all XMLs (ZIP)",
            data=zip_buf,
            file_name=f"otm_orders_import_{int(time.time())}.zip",
            mime="application/zip",
            use_container_width=True
        )

        if payloads:
            st.download_button(
                "⬇️ Download first XML",
                data=payloads[0][4],
                file_name=f"{rows[0]['Order ID']}.xml",
                mime="application/xml",
                use_container_width=True
            )

    st.stop()

# ===== Manual (builder) Mode =====
with st.expander("📄 Header Template", expanded=True):
    domain = st.text_input("DomainName", value="THG")
    base_release_xid = st.text_input("Base XID (SO prefix or PO XID)", value="SO_09000-1128" if order_kind=="Sales Orders" else "PO_09000-1128")
    currency = st.text_input("Currency", value="USD")

with st.expander("📍 Locations & Items", expanded=True):
    if order_kind == "Sales Orders":
        ship_from_label = "ShipFrom (Your DC) XID"
        ship_from_xid = st.text_input(ship_from_label, value="110")
        ship_to_label = "ShipTo (Customers) XIDs"
        ship_to_default = "10000000000013\n10000000000027"
        suppliers_text = ""  # not used for SO
    else:
        suppliers_text = st.text_area("Supplier ShipFrom XIDs (one per line)", value="300000016179177\n300000016179200", height=100)
        ship_from_xid = ""  # not used directly; picked from suppliers list
        ship_to_label = "ShipTo (Your DC) XID(s) — first value will be used"
        ship_to_default = "110"

    ship_to_text = st.text_area(ship_to_label, value=ship_to_default, height=80)
    item_text = st.text_area("PackagedItemGid XIDs (comma/newline)", value="400000002438186\n300000005438196", height=120)

with st.expander("🧩 GID & Line-ID Options", expanded=True):
    use_release_suffix_in_gid = st.checkbox("Add release suffix (_R#) to Release/PO XID", value=False)
    use_release_suffix_in_line_ids = st.checkbox("Add release suffix (_R#) to SO LINE IDs", value=False)

with st.expander("🎚️ Generation Controls", expanded=True):
    releases = st.number_input("How many orders", min_value=1, max_value=1000, value=2, step=1)
    min_lines = st.number_input("Min lines per order", min_value=1, max_value=100, value=2, step=1)
    max_lines = st.number_input("Max lines per order", min_value=min_lines, max_value=100, value=3, step=1)
    min_qty = st.number_input("Min quantity", min_value=1, max_value=10_000_000, value=500, step=1)
    max_qty = st.number_input("Max quantity", min_value=min_qty, max_value=10_000_000, value=3000, step=1)
    min_val = st.number_input("Min declared value", min_value=1, max_value=10_000_000, value=1000, step=1)
    max_val = st.number_input("Max declared value", min_value=min_val, max_value=10_000_000, value=15000, step=1)
    seed = st.number_input("Random seed", min_value=0, max_value=1_000_000, value=42, step=1)
    use_gzip = st.checkbox("Send gzipped XML (Content-Encoding: gzip)", value=False)

# Buttons
col_run1, col_run2 = st.columns(2)
generate_btn = col_run1.button("Generate XMLs")
post_btn = col_run2.button("Generate & POST to OTM")

# ========= Manual Work =========
if generate_btn or post_btn:
    ship_to_list = _parse_list(ship_to_text)
    item_list = _parse_list(item_text)
    supplier_list = _parse_list(suppliers_text) if order_kind == "Purchase Orders" else []

    # Validations
    errors = []
    if not ship_to_list:
        errors.append("Provide at least one ShipTo XID.")
    if not item_list:
        errors.append("Provide at least one PackagedItemGid XID.")
    if order_kind == "Sales Orders" and not ship_from_xid:
        errors.append("Provide ShipFrom (your DC) for Sales Orders.")
    if order_kind == "Purchase Orders" and not supplier_list:
        errors.append("Provide at least one Supplier ShipFrom XID.")
    if max_lines < min_lines:
        errors.append("Max lines cannot be less than Min lines.")
    if max_qty < min_qty:
        errors.append("Max quantity cannot be less than Min quantity.")
    if max_val < min_val:
        errors.append("Max declared value cannot be less than Min declared value.")
    if post_btn and not dry_run:
        if not (otm_url and otm_user and otm_pass):
            errors.append("To POST, provide OTM Endpoint, Username, and Password (or enable Dry run).")
        if otm_url and not is_nonprod_url(otm_url):
            errors.append("POST blocked: OTM Endpoint must contain 'dev' or 'test'.")

    if int(releases) > 1 and not use_release_suffix_in_gid:
        st.warning("⚠️ Multiple orders without suffix may create duplicate IDs. Consider enabling _R#.")

    if errors:
        for e in errors:
            st.error(e)
        st.stop()

    random.seed(int(seed))
    payloads = []
    rows = []
    last_xml = None

    for r in range(1, int(releases) + 1):
        num_lines = random.randint(int(min_lines), int(max_lines))
        # Build SO-shaped line dicts first
        so_lines = []
        for idx in range(1, num_lines + 1):
            item = random.choice(item_list)
            qty = random.randint(int(min_qty), int(max_qty))
            val = random.randint(int(min_val), int(max_val))
            # optional suffix in line IDs (if enabled)
            prefix = f"{base_release_xid}_R{r}" if use_release_suffix_in_line_ids else base_release_xid
            line_xid = f"{prefix}_{idx:03d}"
            so_lines.append({"item_xid": item, "qty": qty, "value": val, "currency": currency, "line_xid": line_xid})

        if order_kind == "Sales Orders":
            ship_to = random.choice(ship_to_list)
            xml_bytes = build_release_xml(
                domain=domain,
                base_release_xid=base_release_xid,
                ship_from_xid=ship_from_xid,
                ship_to_xid=ship_to,
                lines=so_lines,
                release_index=r,
                use_release_suffix_in_gid=use_release_suffix_in_gid,
                use_release_suffix_in_line_ids=use_release_suffix_in_line_ids,
                currency=currency,
            )
            human_id = f"{base_release_xid}_R{r}" if use_release_suffix_in_gid else base_release_xid
            ship_from_display = ship_from_xid
            ship_to_display = ship_to
        else:
            # PO multi-supplier: choose supplier per order
            supplier_from = random.choice(supplier_list)
            dc_ship_to = ship_to_list[0]  # first DC listed

            # Convert to PO line shape
            po_lines = []
            for idx, line in enumerate(so_lines, start=1):
                po_lines.append({
                    "packaged_item_xid": line["item_xid"],
                    "qty": line["qty"],
                    "declared_value": line["value"],
                    "item_number": line["item_xid"],  # map as needed
                    "line_number": idx,
                    "schedule_number": 1,
                    "currency": line.get("currency", currency),
                })

            po_xid = f"{base_release_xid}_R{r}" if use_release_suffix_in_gid else base_release_xid
            xml_bytes = build_purchase_order_xml(
                domain=domain,
                po_xid=po_xid,
                release_method_xid=f"AUTO_CALC - {domain}",
                supplier_ship_from_xid=supplier_from,   # supplier (random)
                dc_ship_to_xid=dc_ship_to,             # your DC
                lines=po_lines,
                currency=currency,
                rate_to_base=1.0,
                func_currency_amount=0.0,
                early_pickup_dt="20250718102700",
                late_pickup_dt="20250725102700",
                tz_id="Asia/Taipei",
                tz_offset="+08:00",
                plan_from_location_xid="CNNGB",
            )
            human_id = po_xid
            ship_from_display = supplier_from
            ship_to_display = dc_ship_to

        payloads.append((human_id, ship_from_display, ship_to_display, so_lines, xml_bytes))
        last_xml = xml_bytes

    # Optional POST
    for rid, ship_from, ship_to, lines, xml_bytes in payloads:
        status, snippet = ("NOT_POSTED", "(dry run)")
        if post_btn and not dry_run:
            try:
                ack = post_to_otm(otm_url, otm_user, otm_pass, xml_bytes, gzip_payload=use_gzip)
                status, snippet = parse_ack_for_status(ack)
            except requests.HTTPError as e:
                resp = e.response
                body = ""
                try:
                    body = resp.text[:1000] if resp is not None else ""
                except Exception:
                    pass
                status = f"HTTP_ERROR {getattr(resp, 'status_code', '')}"
                snippet = f"{e} :: {body}"
            except requests.RequestException as e:
                status = "NETWORK_ERROR"
                snippet = str(e)[:1000]
            except Exception as e:
                status = "APP_ERROR"
                snippet = str(e)[:1000]

        rows.append({
            "Order Kind": "SO" if order_kind == "Sales Orders" else "PO",
            "Order ID": rid,
            "Ship From": ship_from,
            "Ship To": ship_to,
            "# Lines": len(lines),
            "Posted?": "Yes" if (post_btn and not dry_run) else "No",
            "Status": status,
            "Ack / Note": snippet
        })

    # ZIP download of all XMLs
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rid, _, _, _, xml_bytes in payloads:
            zf.writestr(f"{rid}.xml", xml_bytes)
    zip_buf.seek(0)

    st.success(f"Generated {len(payloads)} order(s).")
    st.dataframe(rows, use_container_width=True)
    st.download_button(
        "⬇️ Download all XMLs (ZIP)",
        data=zip_buf,
        file_name=f"otm_orders_{int(time.time())}.zip",
        mime="application/zip",
        use_container_width=True
    )

    if last_xml:
        st.download_button(
            "⬇️ Download last XML",
            data=last_xml,
            file_name="last_order.xml",
            mime="application/xml",
            use_container_width=True
        )

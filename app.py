from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
import io

import streamlit as st
import pandas as pd

st.set_page_config(page_title="Wealth Coach PRO", page_icon="🦄", layout="centered")

st.title("🦄 Wealth Coach PRO (Romania)")
st.info("🚀 Vrei versiunea Premium (salvare plan + PDF)?")

st.markdown("""
👉 **Înscrie-te aici pentru acces Beta:**
[Acces Premium Beta](https://docs.google.com/forms/d/e/1FAIpQLSdvJXLI8UZPHfRUExIScscqqWcWHh8wVELi8J3BmIFsMsU5gQ/viewform?usp=publish-editor)
""")

st.caption("Predicții • scenarii • plan de acțiune — fără API key, 100% local")

# ---- Inputs
st.subheader("1) Datele tale")
st.subheader("0) Buget lunar (pentru un plan profesional)")

colA, colB, colC = st.columns(3)
with colA:
    income = st.number_input("💼 Venit lunar (lei)", min_value=0, value=5000, step=100)
with colB:
    expenses = st.number_input("🧾 Cheltuieli lunare (lei)", min_value=0, value=3500, step=100)
with colC:
    buffer_pct = st.slider("🛟 Buffer siguranță (%)", 0, 30, 10)

available = max(0, income - expenses)
safe_available = int(available * (1 - buffer_pct/100))

st.caption(f"Disponibil după cheltuieli: **{available:,} lei/lună** | După buffer: **{safe_available:,} lei/lună**")

use_safe = st.checkbox("Folosește automat suma disponibilă (după buffer) ca investiție lunară", value=False)
if use_safe:
    monthly = safe_available

monthly = st.number_input("💸 Investiție lunară (lei)", min_value=0, value=500, step=50)
years = st.number_input("📅 Orizont (ani)", min_value=1, value=10, step=1)

col1, col2 = st.columns(2)
with col1:
    interest = st.slider("📈 Randament anual (%)", 1, 20, 8)
with col2:
    inflation = st.slider("📉 Inflație anuală (%)", 0, 15, 5)

fees = st.slider("🏦 Costuri/fee-uri anuale (%)", 0.0, 3.0, 0.5, 0.1)

st.subheader("2) Obiectiv")
goal = st.number_input("🎯 Țintă (lei) — ex: 1.000.000", min_value=0, value=1_000_000, step=50_000)

st.divider()

# ---- Core simulation
def simulate(monthly_lei: float, years: int, annual_return_pct: float):
    months = years * 12
    r = (annual_return_pct / 100) / 12
    balance = 0.0
    series = []
    for m in range(1, months + 1):
        balance = balance * (1 + r) + monthly_lei
        series.append(balance)
    return series  # list of balances

net_return = max(0.0, interest - fees)  # simplistic, but good for MVP
nominal = simulate(monthly, years, net_return)

# adjust for inflation (real value)
real_return = max(0.0, net_return - inflation)
real = simulate(monthly, years, real_return)

months = years * 12
df = pd.DataFrame({
    "Luna": list(range(1, months + 1)),
    "Valoare nominală (lei)": nominal,
    "Valoare reală (lei, după inflație)": real
}).set_index("Luna")

final_nominal = nominal[-1]
final_real = real[-1]
total_contrib = monthly * 12 * years
growth = final_nominal - total_contrib

# ---- Headline KPIs
k1, k2, k3 = st.columns(3)
k1.metric("Depuneri totale", f"{int(total_contrib):,} lei")
k2.metric("Valoare finală (nominal)", f"{int(final_nominal):,} lei")
k3.metric("Câștig (peste depuneri)", f"{int(growth):,} lei")

st.divider()

# ---- Chart
st.subheader("📊 Evoluția în timp")
st.line_chart(df)

# ---- Scenario analysis (WTF factor)
st.subheader("🧪 Scenarii (WTF factor)")

scenarios = [
    ("🐢 Conservator", max(0.0, net_return - 3.0)),
    ("📌 Bază", net_return),
    ("🚀 Optimist", net_return + 3.0),
]
rows = []
for name, r in scenarios:
    s = simulate(monthly, years, r)
    rows.append({
        "Scenariu": name,
        "Randament anual net (%)": round(r, 2),
        "Valoare finală (lei)": int(s[-1])
    })
sc_df = pd.DataFrame(rows)
st.dataframe(sc_df, use_container_width=True)

# ---- When you hit the goal
def month_to_hit(series, target):
    for i, v in enumerate(series):
        if v >= target:
            return i + 1
    return None

hit_m = month_to_hit(nominal, goal)
if goal > 0:
    if hit_m:
        st.success(f"🎉 Ținta de {int(goal):,} lei este atinsă în luna {hit_m} (~ {round(hit_m/12, 1)} ani).")
    else:
        st.warning(f"⏳ Nu atingi {int(goal):,} lei în {years} ani la setările actuale.")

# ---- Reverse: monthly needed to hit goal
def required_monthly(target, years, annual_return_pct, max_iter=60):
    if target <= 0:
        return 0
    lo, hi = 0.0, max(1000.0, target / (years*12)) * 5  # rough upper bound
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        end = simulate(mid, years, annual_return_pct)[-1]
        if end >= target:
            hi = mid
        else:
            lo = mid
    return hi

needed = required_monthly(goal, years, net_return)
st.info(f"🧠 Ca să atingi {int(goal):,} lei în {years} ani (net {net_return:.2f}%/an), ai nevoie de ~ **{int(needed):,} lei/lună**.")

# ---- Action plan (rule-based coaching)
st.subheader("✅ Plan de acțiune (Coach PRO)")
tips = []

if monthly < 300:
    tips.append("Crește investiția lunară cu +100 lei. Diferența pe 10 ani este uriașă.")
if years < 7:
    tips.append("Extinde orizontul la 10–15 ani. Compunerea (compounding) îți face munca grea.")
if net_return < 6:
    tips.append("Caută instrumente cu costuri mici (fee-uri) și randament mediu 7–10% (ex: ETF-uri globale).")
if inflation >= 6:
    tips.append("În perioade cu inflație mare, urmărește creșterea aportului lunar anual (indexare).")
if fees > 1.0:
    tips.append("Redu costurile. Diferența dintre 0.5% și 2% pe an îți poate mânca zeci/sute de mii de lei.")

# “Indexare” - simulate increasing monthly contribution annually
st.subheader("📈 Indexare (contribuție crește anual)")
raise_pct = st.slider("Creștere anuală a contribuției (%)", 0, 20, 5)
def simulate_indexed(monthly_lei, years, annual_return_pct, raise_pct):
    months = years * 12
    r = (annual_return_pct / 100) / 12
    balance = 0.0
    cur = monthly_lei
    series = []
    for m in range(1, months + 1):
        # every 12 months, increase contribution
        if m % 12 == 1 and m != 1:
            cur *= (1 + raise_pct/100)
        balance = balance * (1 + r) + cur
        series.append(balance)
    return series

indexed = simulate_indexed(monthly, years, net_return, raise_pct)
st.success(f"🔥 Cu indexare {raise_pct}%/an, ajungi la: **{int(indexed[-1]):,} lei** (vs {int(final_nominal):,} lei).")

if raise_pct >= 5:
    tips.append(f"Indexează contribuția cu {raise_pct}%/an — e unul dintre cele mai puternice hack-uri reale.")
    def generate_pdf(report):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )
    styles = getSampleStyleSheet()
    elements = []

    elements.append(Paragraph("Wealth Coach PRO — Raport financiar (Beta)", styles["Title"]))
    elements.append(Spacer(1, 12))

    elements.append(Paragraph("<b>Rezumat</b>", styles["Heading2"]))
    summary_tbl = Table([
        ["Venit lunar", f"{report['income']:,} lei"],
        ["Cheltuieli lunare", f"{report['expenses']:,} lei"],
        ["Disponibil (după cheltuieli)", f"{report['available']:,} lei"],
        ["Investiție lunară folosită", f"{report['monthly']:,} lei"],
        ["Orizont", f"{report['years']} ani"],
        ["Randament anual net (%)", f"{report['net_return']:.2f}%"],
        ["Inflație (%)", f"{report['inflation']}%"],
        ["Costuri/fee-uri (%)", f"{report['fees']}%"],
    ], colWidths=[220, 260])

    summary_tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.whitesmoke, colors.lightgrey]),
    ]))
    elements.append(summary_tbl)
    elements.append(Spacer(1, 12))

    elements.append(Paragraph("<b>Rezultate</b>", styles["Heading2"]))
    elements.append(Paragraph(f"Depuneri totale: <b>{report['total_contrib']:,} lei</b>", styles["BodyText"]))
    elements.append(Paragraph(f"Valoare finală (nominal): <b>{report['final_nominal']:,} lei</b>", styles["BodyText"]))
    elements.append(Paragraph(f"Valoare finală (real, după inflație): <b>{report['final_real']:,} lei</b>", styles["BodyText"]))
    elements.append(Paragraph(f"Câștig peste depuneri: <b>{report['growth']:,} lei</b>", styles["BodyText"]))
    elements.append(Spacer(1, 10))

    if report["hit_years"] is not None:
        elements.append(Paragraph(
            f"Ținta de <b>{report['goal']:,} lei</b> este atinsă în ~ <b>{report['hit_years']}</b> ani.",
            styles["BodyText"]
        ))
    else:
        elements.append(Paragraph(
            f"Ținta de <b>{report['goal']:,} lei</b> NU este atinsă în {report['years']} ani la setările actuale.",
            styles["BodyText"]
        ))

    elements.append(Paragraph(
        f"Investiție lunară necesară pentru țintă în {report['years']} ani: "
        f"<b>{report['needed_monthly']:,} lei/lună</b>",
        styles["BodyText"]
    ))
    elements.append(Spacer(1, 12))

    elements.append(Paragraph("<b>Scenarii</b>", styles["Heading2"]))
    scen_tbl = Table(
        [["Scenariu", "Randament net", "Valoare finală"]] + report["scenario_rows"],
        colWidths=[180, 140, 160]
    )
    scen_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.black),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
    ]))
    elements.append(scen_tbl)
    elements.append(Spacer(1, 12))

    elements.append(Paragraph("<b>Recomandări (Coach)</b>", styles["Heading2"]))
    for tip in report["tips"]:
        elements.append(Paragraph("• " + tip, styles["BodyText"]))

    doc.build(elements)
    buffer.seek(0)
    return buffer

def generate_pdf(monthly, years, final_amount):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer)
    elements = []
    styles = getSampleStyleSheet()

    elements.append(Paragraph("Wealth Coach PRO - Plan Financiar", styles['Title']))
    elements.append(Spacer(1, 0.5 * inch))
    elements.append(Paragraph(f"Investitie lunara: {monthly} lei", styles['Normal']))
    elements.append(Paragraph(f"Orizont: {years} ani", styles['Normal']))
    elements.append(Paragraph(f"Valoare estimata: {int(final_amount)} lei", styles['Normal']))
    elements.append(Spacer(1, 0.5 * inch))
    elements.append(Paragraph("Recomandare: Continua investitia si creste contributia anual.", styles['Normal']))

    doc.build(elements)
    buffer.seek(0)
    return buffer

st.divider()
st.header("💎 Premium Upgrade")

hit_years = None
if goal > 0 and hit_m:
    hit_years = round(hit_m/12, 1)

scenario_rows = []
for row in sc_df.to_dict(orient="records"):
    scenario_rows.append([
        row["Scenariu"],
        f"{row['Randament anual net (%)']}%",
        f"{row['Valoare finală (lei)']:,} lei"
    ])

report_data = {
    "income": int(income),
    "expenses": int(expenses),
    "available": int(available),
    "monthly": int(monthly),
    "years": int(years),
    "net_return": float(net_return),
    "inflation": int(inflation),
    "fees": float(fees),
    "goal": int(goal),
    "total_contrib": int(total_contrib),
    "final_nominal": int(final_nominal),
    "final_real": int(final_real),
    "growth": int(growth),
    "hit_years": hit_years,
    "needed_monthly": int(needed),
    "scenario_rows": scenario_rows,
    "tips": tips,
}

st.markdown("""
### 💎 Premium (39 lei) — Raport PDF profesional
Primești un raport pe care îl poți printa și folosi ca plan de acțiune:
- ✅ Buget lunar (venit/cheltuieli) + investiție realistă
- ✅ Predicție nominal vs real (după inflație)
- ✅ Scenarii (Conservator/Bază/Optimist)
- ✅ Când atingi 1.000.000 lei + ce sumă îți trebuie lunar
- ✅ Recomandări clare (următorii pași)

**Bonus (Beta):** acces la următoarele funcții înainte de lansare.
""")

st.markdown("👉 **Cumpără Premium:** [💳 Cumpără Premium – 39 lei](https://buy.stripe.com/test_cNi8wO92W0ohgyb79uc3m00)")
st.caption("🔒 După plată, primești codul Premium. Dacă ai plătit și nu ai cod, scrie pe email/DM și îl trimit imediat.")

st.subheader("🔒 Acces Premium")
code = st.text_input("Cod Premium (primit după plată)", type="password")

PREMIUM_CODE = "UNICORN39"  # schimbă-l când vrei
is_premium = (code.strip() == PREMIUM_CODE)


st.subheader("🔒 Acces Premium")
code = st.text_input("Cod Premium (primit după plată)", type="password")

PREMIUM_CODE = "UNICORN39"  # schimbă-l când vrei
is_premium = (code == PREMIUM_CODE)

if not is_premium:
    st.warning("Pentru PDF ai nevoie de Premium. După plată primești codul pe email/DM.")

# --- PDF Premium (gating) - varianta fără else (anti-indent error)
if not is_premium:
    st.warning("Pentru PDF ai nevoie de Premium. După plată primești codul pe email/DM.")
    st.stop()

if st.button("📄 Generează Plan PDF (Premium)"):
    pdf_file = generate_pdf(report_data)
    st.download_button(
        "⬇️ Download PDF",
        data=pdf_file,
        file_name="wealth_plan.pdf",
        mime="application/pdf"
    )

st.markdown("""
### 💎 Premium (39 lei)
Primești instant:
- 📄 PDF personalizat cu planul tău (ready to print)
- 🎯 Când atingi 1.000.000 lei + ce sumă îți trebuie lunar
- 📈 Scenarii Conservator/Bază/Optimist
- 🚀 Indexare contribuție (hack-ul care accelerează tot)

👉 După plată primești un cod de acces pe email.
""")

if not tips:
    tips.append("Ești setat bine. Ține-te de plan, evită retragerile și optimizează costurile.")

for t in tips:
    st.write("• " + t)

st.divider()
st.caption("💡 Următorul pas de startup: conturi utilizatori + salvare plan + export PDF + abonament.")















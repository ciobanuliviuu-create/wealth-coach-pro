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

if not tips:
    tips.append("Ești setat bine. Ține-te de plan, evită retragerile și optimizează costurile.")

for t in tips:
    st.write("• " + t)

st.divider()
st.caption("💡 Următorul pas de startup: conturi utilizatori + salvare plan + export PDF + abonament.")



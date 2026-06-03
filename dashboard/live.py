"""
Store Intelligence — Live Dashboard (Premium Edition)

# PROMPT:
# "Create a highly advanced, premium analytics dashboard in Streamlit
#  that displays multi-store KPIs, demographic breakdowns, real-time heatmaps,
#  conversion funnels, active anomalies, and camera health status."
#
# CHANGES MADE:
# - Implemented multi-store selection support (ST1008, STORE_1, STORE_2)
# - Added demographic distributions (gender, age buckets) using beautiful Plotly charts
# - Designed premium glassmorphic cards and polished layout with CSS injection
# - Enriched funnel visualization with drop-offs and POS metrics
# - Added per-camera health status logs
"""

import os
import time
import requests
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd

API_URL = os.environ.get("API_URL", "http://localhost:8000")
REFRESH_INTERVAL = 5  # seconds

# ---------------------------------------------------------------------------
# Page Config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Store Intelligence — Live Analytics",
    page_icon="🏪",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Custom CSS for Premium Glassmorphic Look
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }
    
    .main { 
        background-color: #0b0f19; 
    }
    
    .glass-card {
        background: rgba(17, 25, 40, 0.75);
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        border-radius: 16px;
        border: 1px solid rgba(255, 255, 255, 0.075);
        padding: 24px;
        margin-bottom: 20px;
        box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
    }
    
    .glass-card-header {
        font-size: 1.1rem;
        font-weight: 600;
        color: #94a3b8;
        margin-bottom: 12px;
        text-transform: uppercase;
        letter-spacing: 1.5px;
    }
    
    .metric-value-huge {
        font-size: 3rem;
        font-weight: 700;
        background: linear-gradient(135deg, #60a5fa 0%, #a78bfa 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        line-height: 1;
    }
    
    .metric-sub {
        font-size: 0.85rem;
        color: #64748b;
        margin-top: 6px;
    }
    
    .anomaly-row {
        padding: 12px 16px;
        border-radius: 8px;
        margin-bottom: 10px;
        border-left: 4px solid;
    }
    
    .anomaly-CRITICAL {
        background: rgba(239, 68, 68, 0.08);
        border-left-color: #ef4444;
        border: 1px solid rgba(239, 68, 68, 0.15);
    }
    
    .anomaly-WARN {
        background: rgba(245, 158, 11, 0.08);
        border-left-color: #f59e0b;
        border: 1px solid rgba(245, 158, 11, 0.15);
    }
    
    .anomaly-INFO {
        background: rgba(59, 130, 246, 0.08);
        border-left-color: #3b82f6;
        border: 1px solid rgba(59, 130, 246, 0.15);
    }
    
    /* Top navbar styling */
    .stAppHeader {
        background-color: #0b0f19;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# API Helper Function
# ---------------------------------------------------------------------------
def fetch_api(endpoint: str) -> dict:
    """Fetch data from the backend API with graceful error handling."""
    try:
        resp = requests.get(f"{API_URL}{endpoint}", timeout=8)
        if resp.status_code == 200:
            return resp.json()
        return {"error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"error": f"Connection Failed: {e}"}

# ---------------------------------------------------------------------------
# Sidebar Navigation and Control
# ---------------------------------------------------------------------------
with st.sidebar:
    st.image("https://img.icons8.com/isometric-line/100/3b82f6/shop.png", width=80)
    st.markdown("<h2 style='margin-top: 0;'>🏪 Store Intelligence</h2>", unsafe_allow_html=True)
    st.markdown("*PS3f-Compliant Production Analytics*")
    st.markdown("---")

    # Fetch store list from API or default to ST1008, STORE_1, STORE_2
    stores_resp = fetch_api("/stores")
    store_list = ["ST1008", "STORE_1", "STORE_2"]
    if "stores" in stores_resp and stores_resp["stores"]:
        # Merge lists preserving order
        for s in stores_resp["stores"]:
            if s not in store_list:
                store_list.append(s)

    selected_store = st.selectbox("🏬 Target Store", store_list)
    api_url_input = st.text_input("🔗 Backend API URL", value=API_URL)
    
    if api_url_input != API_URL:
        API_URL = api_url_input

    auto_refresh = st.checkbox("🔄 Auto-Refresh (5s)", value=True)
    
    st.markdown("---")
    
    # System Health Quickview
    health = fetch_api("/health")
    if "error" not in health:
        status = health.get("status", "unknown")
        status_emoji = {"healthy": "🟢", "degraded": "🟡", "unhealthy": "🔴", "no_data": "⚪"}.get(status, "⚪")
        st.markdown(f"### System Status: {status_emoji} {status.upper()}")
        
        # Check active store warning status
        store_health = next((s for s in health.get("stores", []) if s["store_id"] == selected_store), None)
        if store_health:
            if store_health.get("stale_feed_warning"):
                st.warning("⚠️ Stale Camera Feeds Detected")
            else:
                st.success("✅ Feeds are Fresh")
            
            st.markdown("##### 📹 Live Camera Feeds")
            for cam in store_health.get("cameras", []):
                cam_status = "🔴 Stale" if cam.get("stale") else "🟢 Live"
                st.caption(f"**{cam['camera_id']}**: {cam_status} ({cam.get('event_count', 0)} events)")
    else:
        st.error(f"API Error: {health['error']}")
        
    st.markdown("---")
    st.caption(f"Last updated: {time.strftime('%H:%M:%S')}")

# ---------------------------------------------------------------------------
# Fetch Store Data
# ---------------------------------------------------------------------------
metrics = fetch_api(f"/stores/{selected_store}/metrics")
funnel = fetch_api(f"/stores/{selected_store}/funnel")
heatmap = fetch_api(f"/stores/{selected_store}/heatmap")
anomalies = fetch_api(f"/stores/{selected_store}/anomalies")

# ---------------------------------------------------------------------------
# Header Section
# ---------------------------------------------------------------------------
col_h1, col_h2 = st.columns([2, 1])
with col_h1:
    st.markdown(f"# 📊 Store Analytics — `{selected_store}`")
    st.markdown("Real-time CCTV Intelligence & POS Purchase Correlation Pipeline")

# ---------------------------------------------------------------------------
# Key Metrics Row
# ---------------------------------------------------------------------------
if "error" not in metrics:
    m_col1, m_col2, m_col3, m_col4, m_col5 = st.columns(5)
    
    with m_col1:
        st.markdown(
            f"""
            <div class="glass-card">
                <div class="glass-card-header">👥 Unique Visitors</div>
                <div class="metric-value-huge">{metrics.get("unique_visitors_today", 0)}</div>
                <div class="metric-sub">Deduplicated customer sessions</div>
            </div>
            """,
            unsafe_allow_html=True
        )
        
    with m_col2:
        rate = metrics.get("conversion_rate", 0)
        st.markdown(
            f"""
            <div class="glass-card">
                <div class="glass-card-header">💰 Conversion Rate</div>
                <div class="metric-value-huge">{rate:.2%}</div>
                <div class="metric-sub">North Star conversion metric</div>
            </div>
            """,
            unsafe_allow_html=True
        )
        
    with m_col3:
        st.markdown(
            f"""
            <div class="glass-card">
                <div class="glass-card-header">🛒 Queue Depth</div>
                <div class="metric-value-huge">{metrics.get("queue_depth", 0)}</div>
                <div class="metric-sub">Active billing occupants</div>
            </div>
            """,
            unsafe_allow_html=True
        )
        
    with m_col4:
        abandon = metrics.get("abandonment_rate", 0)
        st.markdown(
            f"""
            <div class="glass-card">
                <div class="glass-card-header">🚪 Abandonment</div>
                <div class="metric-value-huge">{abandon:.2%}</div>
                <div class="metric-sub">Queue joiners who left</div>
            </div>
            """,
            unsafe_allow_html=True
        )
        
    with m_col5:
        freshness = metrics.get("data_freshness", "unknown")
        color = "#10b981" if freshness == "fresh" else "#f59e0b" if freshness == "stale" else "#6b7280"
        st.markdown(
            f"""
            <div class="glass-card">
                <div class="glass-card-header">📡 Ingestion Health</div>
                <div class="metric-value-huge" style="color: {color};">{freshness.upper()}</div>
                <div class="metric-sub">CCTV ingestion status</div>
            </div>
            """,
            unsafe_allow_html=True
        )
else:
    st.warning(f"Could not load store metrics: {metrics.get('error')}")

# ---------------------------------------------------------------------------
# Middle Column Layout: Funnel + Heatmap
# ---------------------------------------------------------------------------
col_left, col_right = st.columns([1, 1])

with col_left:
    st.markdown("### 🔄 Conversion Funnel")
    if "error" not in funnel and funnel.get("stages"):
        stages = funnel["stages"]
        stage_names = [s["stage"] for s in stages]
        stage_counts = [s["count"] for s in stages]
        
        fig = go.Figure(
            go.Funnel(
                y=stage_names,
                x=stage_counts,
                textinfo="value+percent initial",
                marker=dict(
                    color=["#3b82f6", "#6366f1", "#8b5cf6", "#ec4899"],
                ),
                connector=dict(line=dict(color="rgba(255, 255, 255, 0.2)", width=1.5)),
            )
        )
        fig.update_layout(
            template="plotly_dark",
            height=360,
            margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True)
        
        st.caption(
            f"**Total Sessions:** {funnel.get('total_sessions', 0)} | "
            f"**POS Transactions:** {funnel.get('pos_transactions', 0)} | "
            f"**POS Revenue:** ${funnel.get('pos_revenue', 0.0):,.2f}"
        )
    else:
        st.info("No conversion funnel data available yet.")

with col_right:
    st.markdown("### 🗺️ Zone Engagement Heatmap")
    if "error" not in heatmap and heatmap.get("zones"):
        zones = heatmap["zones"]
        # Format Zone Names or fall back to ID
        zone_labels = [z.get("zone_name") or z["zone_id"] for z in zones]
        scores = [z["normalised_score"] for z in zones]
        visits = [z["visit_frequency"] for z in zones]
        dwells_sec = [z["avg_dwell_ms"] / 1000.0 for z in zones]
        
        fig = px.bar(
            x=scores,
            y=zone_labels,
            orientation='h',
            color=scores,
            color_continuous_scale="Viridis",
            labels={"x": "Engagement Score (0-100)", "y": "Zone Name"},
            hover_data={"visits": visits, "dwell": dwells_sec}
        )
        fig.update_layout(
            template="plotly_dark",
            height=360,
            margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No heatmap data available yet.")

# ---------------------------------------------------------------------------
# Demographics Section
# ---------------------------------------------------------------------------
st.markdown("### 🧬 Customer Demographics (CCTV Inference)")
d_col1, d_col2 = st.columns(2)

with d_col1:
    if "error" not in metrics and metrics.get("gender_distribution"):
        gender_data = metrics["gender_distribution"]
        fig = px.pie(
            names=list(gender_data.keys()),
            values=list(gender_data.values()),
            hole=0.4,
            color_discrete_sequence=["#60a5fa", "#f472b6", "#9ca3af"]
        )
        fig.update_layout(
            title="Gender Classification",
            template="plotly_dark",
            height=280,
            margin=dict(l=10, r=10, t=40, b=10),
            paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No gender demographics detected yet.")

with d_col2:
    if "error" not in metrics and metrics.get("age_distribution"):
        age_data = metrics["age_distribution"]
        # Sort keys
        sorted_keys = sorted(age_data.keys())
        sorted_vals = [age_data[k] for k in sorted_keys]
        
        fig = px.bar(
            x=sorted_keys,
            y=sorted_vals,
            labels={"x": "Age Bucket", "y": "Count"},
            color_discrete_sequence=["#a78bfa"]
        )
        fig.update_layout(
            title="Age Distribution Profile",
            template="plotly_dark",
            height=280,
            margin=dict(l=10, r=10, t=40, b=10),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No age demographics detected yet.")

# ---------------------------------------------------------------------------
# Anomalies & Edge Cases Row
# ---------------------------------------------------------------------------
col_a, col_e = st.columns([1, 1])

with col_a:
    st.markdown("### 🚨 Active Anomalies")
    if "error" not in anomalies and anomalies.get("anomalies"):
        for a in anomalies["anomalies"]:
            severity = a.get("severity", "INFO")
            icon = {"CRITICAL": "🔴", "WARN": "🟡", "INFO": "🔵"}.get(severity, "⚪")
            st.markdown(
                f"""
                <div class="anomaly-row anomaly-{severity}">
                    <strong>{icon} {a['type'].replace('_', ' ')}</strong> — <em>{severity}</em><br/>
                    <span style="font-size: 0.9rem; color: #cbd5e1;">{a['evidence']}</span><br/>
                    <span style="font-size: 0.85rem; color: #94a3b8; font-weight: 600;">💡 Suggested Action:</span>
                    <span style="font-size: 0.85rem; color: #94a3b8;">{a['suggested_action']}</span>
                </div>
                """,
                unsafe_allow_html=True
            )
    else:
        st.success("✅ No active anomalies detected. Operations within optimal parameters.")

with col_e:
    st.markdown("### 🎯 PS3f Edge-Case Performance Logs")
    if "error" not in metrics:
        st.markdown(
            f"""
            <div class="glass-card" style="padding: 16px;">
                <table style="width: 100%; border-collapse: collapse; font-size: 0.95rem; color: #f1f5f9;">
                    <tr style="border-bottom: 1px solid rgba(255,255,255,0.05); height: 40px;">
                        <td>👥 <strong>Group Entries</strong></td>
                        <td style="text-align: right; color: #60a5fa; font-weight: 600;">{metrics.get("group_entries", 0)}</td>
                    </tr>
                    <tr style="border-bottom: 1px solid rgba(255,255,255,0.05); height: 40px;">
                        <td>🔄 <strong>Visitor Re-entries</strong></td>
                        <td style="text-align: right; color: #a78bfa; font-weight: 600;">{metrics.get("reentry_count", 0)}</td>
                    </tr>
                    <tr style="border-bottom: 1px solid rgba(255,255,255,0.05); height: 40px;">
                        <td>🚫 <strong>Staff Sessions Excluded</strong></td>
                        <td style="text-align: right; color: #ec4899; font-weight: 600;">{metrics.get("staff_count", 0)}</td>
                    </tr>
                    <tr style="border-bottom: 1px solid rgba(255,255,255,0.05); height: 40px;">
                        <td>⏱️ <strong>Avg Queue Wait Time</strong></td>
                        <td style="text-align: right; color: #f59e0b; font-weight: 600;">{metrics.get("avg_queue_wait_seconds", 0)} sec</td>
                    </tr>
                    <tr style="height: 40px;">
                        <td>🛍️ <strong>Total Inferred Footfall</strong></td>
                        <td style="text-align: right; color: #10b981; font-weight: 600;">{metrics.get("total_footfall", 0)}</td>
                    </tr>
                </table>
            </div>
            """,
            unsafe_allow_html=True
        )

# ---------------------------------------------------------------------------
# Auto-Refresh Execution
# ---------------------------------------------------------------------------
if auto_refresh:
    time.sleep(REFRESH_INTERVAL)
    st.rerun()

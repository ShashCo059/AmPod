"""
Test script to demonstrate the refactored clinical graph.
Run with: streamlit run test_graph_demo.py
"""

import streamlit as st
from html import escape

# Copy the CSS and function from the main app
st.set_page_config(page_title="Clinical Graph Demo", layout="wide")

st.markdown("""
<style>
    :root { --bg:#F6F8FA; --surface:#FFFFFF; --surface-secondary:#F8FAFC; --border:#E2E8F0; --border-strong:#CBD5E1; --text-primary:#17212B; --text-secondary:#64748B; --text-muted:#94A3B8; --primary:#0F766E; --primary-hover:#0D9488; --primary-light:#CCFBF1; --success:#15803D; --success-light:#DCFCE7; --warning:#B45309; --warning-light:#FEF3C7; --danger:#B91C1C; --danger-light:#FEE2E2; --info:#2563EB; --info-light:#DBEAFE; --purple:#7C3AED; --purple-light:#EDE9FE; }
    .stApp { background: var(--bg) !important; }
    .block-container { max-width: 100%; padding: 0.75rem 1.75rem 0.35rem !important; }

    /* ENHANCEMENT: New Scrollable Clinical Intelligence Graph */
    .clinical-graph-v2 {
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 8px;
        overflow-y: auto;
        height: 400px;
        padding: 24px;
        margin: 12px 0;
    }
    .clinical-graph-v2::-webkit-scrollbar { width: 8px; }
    .clinical-graph-v2::-webkit-scrollbar-track { background: var(--surface-secondary); border-radius: 4px; }
    .clinical-graph-v2::-webkit-scrollbar-thumb { background: var(--border-strong); border-radius: 4px; }
    .clinical-graph-v2::-webkit-scrollbar-thumb:hover { background: var(--border); }

    .graph-hierarchy-v2 {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 32px;
    }
    .graph-patient-node {
        display: flex;
        justify-content: center;
        margin-bottom: 16px;
    }
    .graph-patient-card {
        background: var(--primary);
        color: white;
        padding: 14px 24px;
        border-radius: 8px;
        font-size: 14px;
        font-weight: 700;
        text-align: center;
        min-width: 140px;
        box-shadow: 0 2px 8px rgba(15, 118, 110, 0.3);
    }
    .graph-connector-main {
        width: 2px;
        height: 24px;
        background: var(--border-strong);
        margin: 0 auto;
    }
    .graph-categories {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 24px;
        width: 100%;
    }
    .graph-category {
        display: flex;
        flex-direction: column;
        align-items: center;
    }
    .graph-category-header {
        color: var(--text-secondary);
        font-size: 9px;
        font-weight: 750;
        letter-spacing: 0.5px;
        text-transform: uppercase;
        margin-bottom: 12px;
        text-align: center;
    }
    .graph-category-content {
        display: flex;
        flex-direction: column;
        gap: 8px;
        width: 100%;
    }
    .graph-node-card {
        background: white;
        border: 2px solid var(--border);
        border-radius: 6px;
        padding: 10px 12px;
        font-size: 11px;
        font-weight: 600;
        text-align: center;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        transition: all 0.2s ease;
    }
    .graph-node-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
    }
    .graph-node-diagnosis {
        background: var(--purple);
        color: white;
        border-color: var(--purple);
    }
    .graph-node-symptom {
        background: var(--info);
        color: white;
        border-color: var(--info);
    }
    .graph-node-risk {
        background: var(--warning);
        color: white;
        border-color: var(--warning);
    }
    .graph-empty-state {
        color: var(--text-secondary);
        font-size: 12px;
        text-align: center;
        padding: 20px;
        background: var(--surface-secondary);
        border-radius: 6px;
        border: 1px dashed var(--border-strong);
    }
</style>
""", unsafe_allow_html=True)

def render_clinical_graph(patient_name, insights):
    """Render scrollable clinical intelligence graph with non-overlapping categorized nodes."""
    # Categorize insights
    diagnoses = []
    symptoms = []
    risk_factors = []
    
    diagnosis_keywords = {"diabetes", "hypertension", "high bp", "cholesterol", "hyperlipidemia", "asthma", "arthritis", "osteoarthritis"}
    symptom_keywords = {"fever", "cough", "fatigue", "headache", "nausea", "chest pain"}
    risk_keywords = {"obesity", "smoking", "smok", "alcohol", "use"}
    
    for insight in insights:
        lowered = insight.lower()
        if any(kw in lowered for kw in diagnosis_keywords):
            diagnoses.append(insight)
        elif any(kw in lowered for kw in symptom_keywords):
            symptoms.append(insight)
        elif any(kw in lowered for kw in risk_keywords):
            risk_factors.append(insight)
        else:
            # Default to symptoms if unclear
            symptoms.append(insight)
    
    # Build HTML structure
    html_content = '<div class="clinical-graph-v2"><div class="graph-hierarchy-v2">'
    
    # Patient node
    html_content += '<div class="graph-patient-node">'
    html_content += f'<div class="graph-patient-card">👤 {escape(patient_name[:30])}</div>'
    html_content += '</div>'
    html_content += '<div class="graph-connector-main"></div>'
    
    # Categories grid
    html_content += '<div class="graph-categories">'
    
    # Diagnoses column
    html_content += '<div class="graph-category">'
    html_content += '<div class="graph-category-header">Diagnoses</div>'
    html_content += '<div class="graph-category-content">'
    if diagnoses:
        for dx in diagnoses:
            html_content += f'<div class="graph-node-card graph-node-diagnosis" title="{escape(dx)}">{escape(dx[:25])}</div>'
    else:
        html_content += '<div class="graph-empty-state">No diagnoses detected</div>'
    html_content += '</div></div>'
    
    # Symptoms column
    html_content += '<div class="graph-category">'
    html_content += '<div class="graph-category-header">Symptoms</div>'
    html_content += '<div class="graph-category-content">'
    if symptoms:
        for sym in symptoms:
            html_content += f'<div class="graph-node-card graph-node-symptom" title="{escape(sym)}">{escape(sym[:25])}</div>'
    else:
        html_content += '<div class="graph-empty-state">No symptoms detected</div>'
    html_content += '</div></div>'
    
    # Risk factors column
    html_content += '<div class="graph-category">'
    html_content += '<div class="graph-category-header">Risk Factors</div>'
    html_content += '<div class="graph-category-content">'
    if risk_factors:
        for risk in risk_factors:
            html_content += f'<div class="graph-node-card graph-node-risk" title="{escape(risk)}">{escape(risk[:25])}</div>'
    else:
        html_content += '<div class="graph-empty-state">No risk factors detected</div>'
    html_content += '</div></div>'
    
    html_content += '</div></div></div>'
    st.markdown(html_content, unsafe_allow_html=True)

# Demo the graph
st.title("Clinical Intelligence Graph - Refactored")
st.write("This demo showcases the new scrollable, non-overlapping clinical graph layout.")

# Test Case 1: Comprehensive example
st.subheader("Test Case 1: Comprehensive Patient Profile")
insights_1 = [
    "Diabetes (Type 2)",
    "Hypertension",
    "Hyperlipidemia",
    "Fever",
    "Chronic Cough",
    "Fatigue",
    "Smoking History",
    "Obesity",
    "Alcohol Use"
]
render_clinical_graph("John Smith", insights_1)

# Test Case 2: Limited insights
st.subheader("Test Case 2: Limited Insights")
insights_2 = [
    "Asthma",
    "Headache"
]
render_clinical_graph("Jane Doe", insights_2)

# Test Case 3: No insights
st.subheader("Test Case 3: No Detected Insights")
insights_3 = []
render_clinical_graph("Robert Johnson", insights_3)

# Test Case 4: Many insights (scroll test)
st.subheader("Test Case 4: Many Insights (Test Scrollability)")
insights_4 = [
    "Diabetes (Type 2)",
    "Hypertension",
    "Asthma",
    "Osteoarthritis",
    "Hyperlipidemia",
    "Fever",
    "Chronic Cough",
    "Fatigue",
    "Headache",
    "Nausea",
    "Chest Pain",
    "Smoking History",
    "Alcohol Use",
    "Obesity"
]
render_clinical_graph("Mary Williams", insights_4)

st.info("✅ The graph now uses a scrollable layout with three categories: Diagnoses (purple), Symptoms (blue), and Risk Factors (orange). Hover over nodes to see the full names.")

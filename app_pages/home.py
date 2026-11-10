"""Home page: dataset upload, application sizing lookup and raw dataset explorer."""

import io
import importlib
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from core.schemas import detect_schema, KNOWN_SCHEMAS
from core.path_manager import (
    find_local_system_path,
    scan_default_local_files,
    load_persistent_paths,
    clear_persistent_paths
)
from core.dataset_loader import load_dataset_from_source, load_sample_data, convert_df_to_excel_bytes
from core import app_lookup, azure_recommender
importlib.reload(app_lookup)
importlib.reload(azure_recommender)
from core.app_lookup import extract_app_numbers, lookup_application, generate_app_excel_bytes
from core.azure_recommender import recommend_azure_postgresql, extract_domain_from_fqdn, select_azure_sku, generate_azure_markdown_report


# Automatic restoration from saved persistent paths on startup
if "initialized_from_saved_paths" not in st.session_state:
    st.session_state.initialized_from_saved_paths = True
    saved_initial_paths = load_persistent_paths()
    slot_schemas_init = {
        "arm": "ARM SERVICE NOW FILE",
        "ba": "BA-AS-A-SERVER",
        "cmdb": "CMDB-CI-SERVER"
    }
    for s_slot, s_path in saved_initial_paths.items():
        if Path(s_path).exists():
            load_dataset_from_source(s_path, expected_schema=slot_schemas_init.get(s_slot), slot_key=s_slot)


# ---------------- MAIN CONTENT ----------------
st.title("Azure migration assistant")
st.caption("Application hardware sizing & target architecture planner")

# Single upload section on main page
if "uploader_version" not in st.session_state:
    st.session_state.uploader_version = 0

REQUIRED_DATASETS = [
    ("ARM Service Now", "ARM SERVICE NOW FILE", "ARM Service now"),
    ("BA_AS_SERVER", "BA-AS-A-SERVER", "ba_as_server"),
    ("CMDB_CI_SERVER", "CMDB-CI-SERVER", "cmdb_ci_server"),
]

with st.container(border=True):
    col_u_hdr, col_u_actions = st.columns([2.5, 2.5], vertical_alignment="center")
    with col_u_hdr:
        st.subheader("Upload datasets")
        st.caption("Drop all your Excel or CSV files at once. Each file is detected by name and schema.")
    with col_u_actions:
        c_btn_local, c_btn_sample, c_btn_clear = st.columns(3)
        with c_btn_local:
            if st.button("Load local", icon=":material/bolt:", width="stretch", help="Load detected files from local system and save paths"):
                scanned = scan_default_local_files()
                slot_schemas = {"arm": "ARM SERVICE NOW FILE", "ba": "BA-AS-A-SERVER", "cmdb": "CMDB-CI-SERVER"}
                for s_key, s_path in scanned.items():
                    if s_path and s_path.exists():
                        load_dataset_from_source(s_path, expected_schema=slot_schemas.get(s_key), slot_key=s_key)
                st.session_state.active_app_num = ""
                st.rerun()
        with c_btn_sample:
            if st.button("Sample data", icon=":material/science:", width="stretch", help="Load built-in mock datasets"):
                load_sample_data()
                st.session_state.active_app_num = ""
                st.rerun()
        with c_btn_clear:
            if st.button("Clear all", icon=":material/delete:", width="stretch", help="Clear all loaded datasets and saved path configuration"):
                st.session_state.datasets.clear()
                clear_persistent_paths()
                st.session_state.active_app_num = ""
                st.session_state.pop("last_upload_names", None)
                st.session_state.uploader_version += 1  # reset uploader widget
                st.rerun()

    uploaded_files = st.file_uploader(
        "Drop or upload Excel or CSV files",
        type=["xlsx", "xls", "csv"],
        accept_multiple_files=True,
        key=f"uploader_all_{st.session_state.uploader_version}",
        help="One upload for every dataset. Files are matched to slots by their name."
    )

    # Sync uploaded files with loaded datasets (one dataset per detected schema)
    cur_names = [f.name for f in uploaded_files]
    prev_names = st.session_state.get("last_upload_names", [])
    if cur_names != prev_names:
        for name in prev_names:
            if name not in cur_names:
                st.session_state.datasets.pop(name, None)
        for f in uploaded_files:
            if f.name not in prev_names:
                schema = detect_schema(f.name)
                if schema != "Custom / Unknown":
                    for k in [k for k, v in st.session_state.datasets.items() if v.get("detected_schema") == schema]:
                        del st.session_state.datasets[k]
                load_dataset_from_source(f)
        st.session_state["last_upload_names"] = cur_names
        st.session_state.active_app_num = ""
        st.rerun()

    # Status of the three required datasets
    status_cols = st.columns(3)
    for col, (label, schema_key, keyword) in zip(status_cols, REQUIRED_DATASETS):
        with col:
            with st.container(border=True):
                st.markdown(f"**{label}**")
                meta_entry = next(((k, v) for k, v in st.session_state.datasets.items() if v.get("detected_schema") == schema_key), None)
                if meta_entry:
                    fname, meta = meta_entry
                    st.badge("Loaded", icon=":material/check_circle:", color="green")
                    st.caption(f"`{fname}` | Rows: **{len(meta['df']):,}**")
                else:
                    st.badge("Missing", icon=":material/warning:", color="orange")
                    st.caption(f"Upload a file with `{keyword}` in its name.")

if not st.session_state.datasets:
    # Empty State Guidance
    st.info("Upload the Excel or CSV files above for **ARM Service Now**, **BA_AS_SERVER**, and **CMDB_CI_SERVER**, or click **Sample data** to preview application sizing immediately.", title="Getting started", icon=":material/lightbulb:")
else:
    # Top-level Tabs
    main_tab_lookup, main_tab_explorer = st.tabs([":material/search: Application sizing lookup", ":material/table_chart: Raw datasets explorer"])

    # ================= TAB 1: APPLICATION LOOKUP =================
    with main_tab_lookup:
        st.subheader("Application sizing & hardware lookup")
        start = time.perf_counter()
        available_apps = extract_app_numbers(st.session_state.datasets)
        print(f"Extract application numbers: {time.perf_counter() - start:.3f}s")
        col_search1, col_search2, col_btn = st.columns([3, 2, 1])
        with col_search1:
            entered_app = st.text_input(
                "Enter Application Number:",
                value="",
                placeholder="Type application number (e.g. APP0001)...",
                help="Type the application number and press Enter or click Look Up"
            )
        with col_search2:
            if available_apps:
                picked_app = st.selectbox(
                    "Or pick from detected applications:",
                    options=[""] + available_apps,
                    index=0,
                    format_func=lambda x: "Select from detected list..." if x == "" else x,
                    help="Application numbers detected in your loaded datasets"
                )
            else:
                picked_app = ""
        with col_btn:
            st.write("")
            st.write("")
            lookup_clicked = st.button("Look up", icon=":material/search:", width="stretch")

        # Resolve selected application:
        # Priority: explicit typed text, then dropdown selection
        selected_app_num = None
        if entered_app.strip():
            selected_app_num = entered_app.strip()
        elif picked_app:
            selected_app_num = picked_app

        if selected_app_num:
            dataset_version = st.session_state.get("datasets_version", 0)
            lookup_key = (dataset_version, selected_app_num)

            cached = st.session_state.get("application_lookup_result")

            needs_lookup = (
                cached is None
                or cached["key"] != lookup_key
                or lookup_clicked
            )

            if needs_lookup:
                start = time.perf_counter()

                app_info, servers_df, rollup = lookup_application(
                    selected_app_num,
                    st.session_state.datasets
                )

                st.session_state["application_lookup_result"] = {
                    "key": lookup_key,
                    "app_info": app_info,
                    "servers_df": servers_df,
                    "rollup": rollup,
                }

                print(
                    f"Application lookup: "
                    f"{time.perf_counter() - start:.3f}s"
                )
            else:
                print("Application lookup: reused saved result")

            result = st.session_state["application_lookup_result"]
            app_info = result["app_info"]
            servers_df = result["servers_df"]
            rollup = result["rollup"]
            print(f"Application lookup: {time.perf_counter() - start:.3f}s")

            app_name = app_info.get("Name", "Name Not Specified in Catalog")
            env = app_info.get("Environment", "Unknown")
            crit = app_info.get("Business criticality", "Unknown")
            owner = app_info.get("IT Application owner", app_info.get("Business owner", "Not Listed"))
            status = app_info.get("Install Status", "Unknown")
            arch = app_info.get("Architecture type", "N/A")

            # App Title & Metadata Card (Theme-adaptive container)
            with st.container(border=True):
                st.subheader(f"{selected_app_num}: {app_name}")
                col_m1, col_m2, col_m3, col_m4, col_m5 = st.columns(5)
                col_m1.markdown(f"**Environment**\n\n`{env}`")
                col_m2.markdown(f"**Criticality**\n\n`{crit}`")
                col_m3.markdown(f"**Owner**\n\n{owner}")
                col_m4.markdown(f"**Status**\n\n`{status}`")
                col_m5.markdown(f"**Architecture**\n\n`{arch}`")

            # Hardware Rollup KPIs
            st.markdown("#### Hardware resource rollup")
            k1, k2, k3, k4, k5 = st.columns(5)
            k1.metric("Total Servers", f"{rollup['Total Servers']}", f"{rollup['Virtual Servers']} VMs / {rollup['Physical Servers']} Phys")
            k2.metric("Total vCPUs / Cores", f"{rollup['Total vCPUs']}")
            k3.metric("Total RAM", f"{rollup['Total RAM (GB)']:.1f} GB")
            k4.metric("Allocated Storage", f"{rollup['Total Allocated Disk (GB)']:.1f} GB")
            k5.metric("In-Use / Consumed", f"{rollup['Total In-Use Disk (GB)']:.1f} GB")


            # Mapped Servers Table & Filters
            st.markdown("#### Mapped host servers")

            if not servers_df.empty:
                # 1. Filter by Domain or FQDN
                filter_c1, filter_c2 = st.columns([1, 2])
                all_fqdns = servers_df["Server FQDN"].dropna().tolist()
                domain_list = sorted(list(set(servers_df["Server FQDN"].apply(extract_domain_from_fqdn))))

                with filter_c1:
                    chosen_domain = st.selectbox(
                        "Filter by Domain:",
                        options=["All Domains"] + domain_list,
                        index=0,
                        key=f"dom_filter_{selected_app_num}",
                        help="Filter servers belonging to a specific network domain"
                    )

                if chosen_domain != "All Domains":
                    eligible_fqdns = [f for f in all_fqdns if extract_domain_from_fqdn(f) == chosen_domain]
                else:
                    eligible_fqdns = all_fqdns

                with filter_c2:
                    chosen_fqdns = st.multiselect(
                        "Filter by Server FQDN(s) to compute target sizing:",
                        options=eligible_fqdns,
                        default=eligible_fqdns,
                        key=f"fqdn_filter_{selected_app_num}",
                        help="Select one or more server FQDNs to include in Azure Flexible Server sizing"
                    )

                filtered_servers_df = servers_df[servers_df["Server FQDN"].isin(chosen_fqdns)] if chosen_fqdns else pd.DataFrame(columns=servers_df.columns)

                st.caption(f"Showing **{len(filtered_servers_df)}** of **{len(servers_df)}** mapped host servers")
                st.dataframe(filtered_servers_df, height=220)

                if not filtered_servers_df.empty:
                    # 2. Azure Target Recommendation Section
                    
                    # Workload & Configuration controls
                    is_prod_workload = (env.lower() == "production") or (crit.lower() in ["critical", "high"])
                    
                    with st.container(border=True):
                        cfg_c1, cfg_c2 = st.columns([1.5, 2.0])
                        with cfg_c1:
                            source_engine = st.selectbox(
                                "Source database engine:",
                                options=["IBM Db2", "Generic / Other"],
                                index=0,
                                key=f"engine_{selected_app_num}",
                                help="Informational. The tier comes from the RAM:vCPU ratio, workload type and the advanced inputs below."
                            )
                        with cfg_c2:
                            ha_option = st.radio(
                                "High availability architecture:",
                                options=["Zone-Redundant HA (Dual-Zone Standby)", "Standalone (Single Zone)"],
                                index=0 if is_prod_workload else 1,
                                horizontal=True,
                                key=f"ha_{selected_app_num}",
                                help="Zone-redundant HA automatically provisions a synchronous standby in another AZ."
                            )
                            ha_enabled = "Zone-Redundant" in ha_option

                    with st.expander("Advanced sizing inputs (optional)", icon=":material/tune:"):
                        st.caption(
                            "Leave a field blank to use the default estimate. Each number you add replaces a guess with a "
                            "measurement from your own workload, and the result below shows what it changed."
                        )
                        k = selected_app_num
                        t_cpu, t_mem, t_sto, t_conn = st.tabs(["CPU", "Memory", "Storage and IOPS", "Connections"])
                        with t_cpu:
                            peak_cpu = st.number_input(
                                "Peak CPU utilization of the source (%)", min_value=1.0, max_value=100.0, value=None, step=5.0,
                                placeholder="Unknown", key=f"peak_cpu_{k}",
                                help="Busiest-hour CPU on the source servers. vCores = source cores x peak% / 65% target, so a server that peaks at 30% needs far fewer Azure cores than it has today.",
                            )
                            st.caption("Blank: size from allocated source cores 1:1. Where to find it: Db2 monitoring, OS metrics or your APM.")
                        with t_mem:
                            working_set = st.number_input(
                                "Hot working set (GB)", min_value=1.0, value=None, step=1.0, placeholder="Unknown", key=f"ws_{k}",
                                help="Tables and indexes touched in a normal hour. RAM target = 1.5 x this.",
                            )
                            mem_profile = st.segmented_control(
                                "Memory profile", ["Auto", "General Purpose", "Memory Optimized"], default="Auto", required=True,
                                key=f"memprof_{k}",
                                help="Auto picks E-series only when it reaches the RAM target with fewer vCores. General Purpose = 4 GiB/vCore. Memory Optimized = 8 GiB/vCore.",
                            )
                            st.caption("Blank working set: size from source RAM (x1.25, or 1:1 when the headroom would jump a SKU).")
                        with t_sto:
                            s1, s2 = st.columns(2)
                            growth = s1.number_input(
                                "Data growth per month (GB)", min_value=0.0, value=None, step=1.0, placeholder="Unknown", key=f"growth_{k}",
                                help="Storage is sized for 12 months of growth, then +30% for MVCC/WAL/bloat. Storage can never shrink.",
                            )
                            peak_iops = s2.number_input(
                                "Peak IOPS observed", min_value=1, value=None, step=500, placeholder="Unknown", key=f"iops_{k}",
                                help="Busiest-hour disk IOPS on the source. Provisioned at peak + 30%. May raise the VM size (VM caps) or add paid SSD v2 IOPS.",
                            )
                            st.caption("Effective IOPS = the lower of the disk's IOPS and the VM's cap. Learn how in the Research & guide page, Storage lesson.")
                        with t_conn:
                            c1, c2, c3 = st.columns(3)
                            n_inst = c1.number_input("App instances", min_value=1, value=None, step=1, placeholder="Unknown", key=f"inst_{k}")
                            pool_sz = c2.number_input("Pool size each", min_value=1, value=None, step=1, placeholder="Unknown", key=f"pool_{k}", help="Keep app pools small: 5 to 20.")
                            other_conn = c3.number_input("Workers + admin tools", min_value=0, value=None, step=1, placeholder="0", key=f"otherconn_{k}")
                            st.caption("Total client connections = instances x pool + others. Checked against the server's max_connections.")
                        growth = growth or None
                        client_conns = int(n_inst * pool_sz + (other_conn or 0)) if n_inst and pool_sz else None
                        n_given = sum(v is not None for v in [peak_cpu, working_set, growth, peak_iops, client_conns]) + (mem_profile != "Auto")
                        adv_args = dict(
                            peak_cpu_pct=peak_cpu, working_set_gb=working_set, memory_profile=mem_profile,
                            growth_gb_per_month=growth, peak_iops=peak_iops, client_connections=client_conns,
                        )

                    # Sizing Rollup for filtered servers
                    filt_vcpus = float(filtered_servers_df["vCPUs"].sum())
                    filt_ram = float(filtered_servers_df["RAM (GB)"].sum())
                    filt_disk = float(filtered_servers_df["Allocated Disk (GB)"].sum())
                    if filt_disk <= 0:
                        filt_disk = float(filtered_servers_df["In-Use Disk (GB)"].sum())

                    # Run recommendation engine
                    azure_rec = recommend_azure_postgresql(
                        vcpus=filt_vcpus,
                        ram_gb=filt_ram,
                        disk_gb=filt_disk,
                        source_engine=source_engine,
                        is_production=is_prod_workload,
                        ha_override=ha_enabled,
                        **adv_args
                    )
                    baseline_rec = recommend_azure_postgresql(
                        vcpus=filt_vcpus, ram_gb=filt_ram, disk_gb=filt_disk,
                        source_engine=source_engine, is_production=is_prod_workload, ha_override=ha_enabled,
                    )

                    sku_meta = azure_rec["recommended_sku"]

                    # Precompute reports for download
                    try:
                        app_excel_bytes = generate_app_excel_bytes(app_info, filtered_servers_df, rollup, azure_rec=azure_rec)
                    except TypeError:
                        app_excel_bytes = generate_app_excel_bytes(app_info, filtered_servers_df, rollup)
                    
                    md_report_str = generate_azure_markdown_report(app_info, filtered_servers_df, azure_rec)

                    # Recommendation Header with Top Quick Download
                    col_rec_hdr, col_rec_dl = st.columns([2.6, 1.4])
                    with col_rec_hdr:
                        st.markdown("### :material/cloud: Azure Database for PostgreSQL: sizing recommendation & reasoning")
                        st.caption("Automated architectural sizing and technical reasoning based on Azure Flexible Server best practices.")
                    with col_rec_dl:
                        st.download_button(
                            label="Download sizing report (.xlsx)",
                            icon=":material/download:",
                            data=app_excel_bytes,
                            file_name=f"{selected_app_num}_azure_sizing_report.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            width="stretch",
                            key=f"dl_top_{selected_app_num}"
                        )

                    # Recommendation Card
                    with st.container(border=True):
                        st.markdown(f"#### Recommended target SKU: `{sku_meta['sku']}`")
                        badge_color = "violet" if "Memory" in sku_meta["tier"] else "blue"
                        st.markdown(f"**Tier:** :{badge_color}-badge[{sku_meta['tier']}] (`{sku_meta['family']}`) | **vCPUs:** `{sku_meta['vcpus']}` | **Memory:** `{sku_meta['memory_gib']} GiB` | **Target Storage:** `{azure_rec['target_storage_gib']:,} GiB`")

                        rec_m1, rec_m2, rec_m3, rec_m4 = st.columns(4)
                        rec_m1.metric("Compute SKU", sku_meta["sku"], f"{sku_meta['vcpus']} vCPUs")
                        rec_m2.metric("Target Memory", f"{sku_meta['memory_gib']} GiB", f"{azure_rec['reasoning']['compute_sizing']['headroom_label']} ({filt_ram:.1f} GB src)")
                        rec_m3.metric("Provisioned Storage", f"{azure_rec['target_storage_gib']:,} GiB", f"+30% MVCC/WAL ({filt_disk:.1f} GB src)")
                        rec_m4.metric("High availability", "Zone-Redundant" if azure_rec["ha_enabled"] else "Standalone", "Sync replication" if azure_rec["ha_enabled"] else "Single zone")
                        st.progress(n_given / 6, text=f"Sizing uses {n_given} of 6 measured inputs. The rest are default estimates.")
                        if n_given:
                            b, a = baseline_rec, azure_rec
                            if b["recommended_sku"]["sku"] != a["recommended_sku"]["sku"]:
                                st.info(f"Your inputs moved the SKU from `{b['recommended_sku']['sku']}` to `{a['recommended_sku']['sku']}`.", icon=":material/compare_arrows:")
                            else:
                                st.caption(f"Your inputs kept the SKU at `{a['recommended_sku']['sku']}`. Storage ({b['target_storage_gib']:,} to {a['target_storage_gib']:,} GiB) and IOPS notes may still differ.")
                        if azure_rec.get("extra_iops_to_buy"):
                            st.caption(f":material/shopping_cart: Buy about **{azure_rec['extra_iops_to_buy']:,} extra IOPS** on Premium SSD v2 (billed separately) to cover peak + 30%.")
                        if azure_rec.get("connections"):
                            cn = azure_rec["connections"]
                            (st.warning if cn["pct"] > 80 else st.success)(cn["message"], icon=":material/cable:")
                        if azure_rec.get("capacity_warning"):
                            st.error(azure_rec["capacity_warning"], icon=":material/error:")
                        st.caption(f":material/speed: Effective IOPS: **{azure_rec['effective_iops']:,}** (lower of the disk's free IOPS and the VM cap)" + ("" if azure_rec["reasoning"]["storage_sizing"]["vm_iops_cap"] else ". VM cap for this SKU is not in our data, so it may be lower."))
                        if sku_meta["tier"] == "Burstable":
                            st.warning("Dev/test only. Burstable is not supported for production. Stop the server off-hours.", icon=":material/warning:")

                    # Detailed Architectural Reasoning
                    st.markdown("##### Sizing & architectural reasoning")
                    
                    r_col1, r_col2 = st.columns(2)
                    with r_col1:
                        with st.container(border=True):
                            st.markdown(f"**1. Compute Tier Selection: `{sku_meta['tier']}`**")
                            st.write(azure_rec["reasoning"]["tier_selection"]["justification"])
                            st.caption(f"Source vCPUs: {azure_rec['source_vcpus']:.0f} | Source RAM: {azure_rec['source_ram_gb']:.1f} GB")

                        with st.container(border=True):
                            st.markdown(f"**2. High Availability: `{azure_rec['reasoning']['high_availability']['mode']}`**")
                            st.write(azure_rec["reasoning"]["high_availability"]["justification"])
                            st.caption(f"App Environment: `{env}` | Business Criticality: `{crit}`.")

                    with r_col2:
                        with st.container(border=True):
                            st.markdown(f"**3. Compute & memory sizing: `{azure_rec['target_ram_gib']} GiB` target**")
                            st.write(azure_rec["reasoning"]["compute_sizing"]["justification"])
                            st.caption(f"Target vCPUs: {azure_rec['target_vcpus']} cores | Target RAM: {azure_rec['target_ram_gib']} GiB ({azure_rec['reasoning']['compute_sizing']['headroom_label']}).")

                        with st.container(border=True):
                            st.markdown(f"**4. Storage & MVCC Headroom: `{azure_rec['target_storage_gib']:,} GiB` Target**")
                            st.write(azure_rec["reasoning"]["storage_sizing"]["justification"])
                            st.caption(f"Source Disk: {azure_rec['source_disk_gb']:.1f} GB | 1.30× MVCC safety margin.")

                    # If multiple servers filtered, provide individual server mapping
                    if len(filtered_servers_df) > 1:
                        with st.expander(f"Per-server Azure target breakdown ({len(filtered_servers_df)} servers)", icon=":material/dns:"):
                            indiv_rows = []
                            for _, srow in filtered_servers_df.iterrows():
                                s_fqdn = srow["Server FQDN"]
                                s_cpu = float(srow["vCPUs"])
                                s_ram = float(srow["RAM (GB)"])
                                s_disk = float(srow["Allocated Disk (GB)"]) if float(srow["Allocated Disk (GB)"]) > 0 else float(srow["In-Use Disk (GB)"])
                                s_rec = recommend_azure_postgresql(
                                    vcpus=s_cpu,
                                    ram_gb=s_ram,
                                    disk_gb=s_disk,
                                    source_engine=source_engine,
                                    is_production=is_prod_workload,
                                    ha_override=ha_enabled,
                                    peak_cpu_pct=peak_cpu,
                                    working_set_gb=working_set,
                                    memory_profile=mem_profile
                                )
                                s_sku = s_rec["recommended_sku"]
                                indiv_rows.append({
                                    "Server FQDN": s_fqdn,
                                    "Source Cores": int(s_cpu),
                                    "Source RAM (GB)": s_ram,
                                    "Source Disk (GB)": s_disk,
                                    "Azure SKU": s_sku["sku"],
                                    "Azure Tier": s_sku["tier"],
                                    "SKU Specs": f"{s_sku['vcpus']} vCPU / {s_sku['memory_gib']} GiB RAM",
                                    "Target Storage (GiB)": f"{s_rec['target_storage_gib']:,}",
                                    "HA Mode": "Zone-Redundant" if s_rec["ha_enabled"] else "Standalone"
                                })
                            st.dataframe(pd.DataFrame(indiv_rows), height=200)

                    # Export Center
                    st.markdown("##### Downloadable sizing reports")
                    dl_col_left, dl_col_btn1, dl_col_btn2 = st.columns([2.5, 1.25, 1.25])
                    with dl_col_left:
                        st.markdown(f"**Export Package for {selected_app_num} ({app_name}):**")
                        st.caption(f"Target SKU: `{sku_meta['sku']}` | Sized Storage: `{azure_rec['target_storage_gib']:,} GiB` | Mode: `{azure_rec['reasoning']['high_availability']['mode']}`")
                    with dl_col_btn1:
                        st.download_button(
                            label="Excel report (.xlsx)",
                            icon=":material/table_view:",
                            data=app_excel_bytes,
                            file_name=f"{selected_app_num}_azure_sizing_report.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            width="stretch",
                            key=f"dl_bottom_xlsx_{selected_app_num}"
                        )
                    with dl_col_btn2:
                        st.download_button(
                            label="Markdown brief (.md)",
                            icon=":material/description:",
                            data=md_report_str.encode("utf-8"),
                            file_name=f"{selected_app_num}_azure_sizing_brief.md",
                            mime="text/markdown",
                            width="stretch",
                            key=f"dl_bottom_md_{selected_app_num}"
                        )
                else:
                    st.warning("No servers match the chosen domain or FQDN filter. Select at least one FQDN to compute sizing.")
            else:
                st.warning(f"No servers found mapped to {selected_app_num} in BA-AS-A-SERVER. Ensure BA-AS-A-SERVER dataset is loaded.")

            # Full Application Metadata (Expandable)
            with st.expander("View complete application attributes from ARM catalog", icon=":material/list_alt:"):
                attr_df = pd.DataFrame([{"Attribute": k, "Value": v} for k, v in app_info.items()])
                st.dataframe(attr_df, height=250)
        else:
            st.info("Enter an application number above to compute hardware sizing, server mappings, and generate downloadable reports.", icon=":material/lightbulb:")

    # ================= TAB 2: RAW DATASETS EXPLORER =================
    with main_tab_explorer:
        st.subheader("Raw datasets explorer")

        total_files = len(st.session_state.datasets)
        total_rows = sum(len(m["df"]) for m in st.session_state.datasets.values())
        schema_types = [m["detected_schema"] for m in st.session_state.datasets.values()]
        known_matched = sum(1 for s in schema_types if s != "Custom / Unknown")

        kpi1, kpi2, kpi3 = st.columns(3)
        kpi1.metric("Loaded Files", f"{total_files}")
        kpi2.metric("Total Records Across Files", f"{total_rows:,}")
        kpi3.metric("Recognized Schemas", f"{known_matched} / {total_files}")


        file_list = list(st.session_state.datasets.keys())
        subtabs = st.tabs([f":material/description: {f}" for f in file_list])

        for i, file_name in enumerate(file_list):
            with subtabs[i]:
                meta = st.session_state.datasets[file_name]
                df = meta["df"]
                detected = meta["detected_schema"]

                # Sheet selector if Excel has multiple sheets
                if meta.get("is_excel") and len(meta.get("sheets", [])) > 1:
                    col_sheet, col_empty = st.columns([2, 4])
                    with col_sheet:
                        new_sheet = st.selectbox(
                            f"Select Sheet for {file_name}:",
                            options=meta["sheets"],
                            index=meta["sheets"].index(meta["active_sheet"]),
                            key=f"sheet_select_{file_name}"
                        )
                        if new_sheet != meta["active_sheet"]:
                            meta["active_sheet"] = new_sheet
                            if meta.get("file_path") and Path(meta["file_path"]).exists():
                                new_df = pd.read_excel(meta["file_path"], sheet_name=new_sheet)
                            else:
                                new_df = pd.read_excel(io.BytesIO(meta["file_bytes"]), sheet_name=new_sheet)
                            meta["df"] = new_df
                            meta["detected_schema"] = detect_schema(file_name)
                            st.rerun()

                schema_info = KNOWN_SCHEMAS.get(detected, {})
                desc = schema_info.get("description", "Uploaded custom or unclassified dataset")
                st.info(f"**Detected Type:** `{detected}` — {desc}")

                ctrl_col1, ctrl_col2, ctrl_col3 = st.columns([3, 2, 2])
                with ctrl_col1:
                    search_term = st.text_input(
                        "Search across all columns",
                        key=f"search_{file_name}",
                        placeholder="Type keyword, hostname, app number, IP..."
                    )
                with ctrl_col2:
                    all_cols = list(df.columns)
                    selected_cols = st.multiselect(
                        "Select columns to display",
                        options=all_cols,
                        default=all_cols if len(all_cols) <= 15 else all_cols[:15],
                        key=f"cols_{file_name}",
                        help=f"Total columns available: {len(all_cols)}"
                    )
                with ctrl_col3:
                    st.write("")
                    st.write("")
                    col_d1, col_d2 = st.columns(2)
                    with col_d1:
                        csv_data = df.to_csv(index=False).encode("utf-8")
                        st.download_button(
                            label="CSV",
                            icon=":material/description:",
                            data=csv_data,
                            file_name=f"{file_name.split('.')[0]}.csv",
                            mime="text/csv",
                            key=f"dl_csv_{file_name}"
                        )
                    with col_d2:
                        excel_bytes = convert_df_to_excel_bytes({file_name.split(".")[0]: df})
                        st.download_button(
                            label="Excel",
                            icon=":material/table_view:",
                            data=excel_bytes,
                            file_name=f"{file_name.split('.')[0]}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key=f"dl_xlsx_{file_name}"
                        )

                display_df = df if not selected_cols else df[selected_cols]
                if search_term.strip():
                    mask = display_df.astype(str).apply(
                        lambda row: row.str.contains(search_term.strip(), case=False, na=False).any(),
                        axis=1
                    )
                    filtered_df = display_df[mask]
                    st.caption(f"Showing **{len(filtered_df):,}** of **{len(df):,}** rows matching `'{search_term}'`")
                else:
                    filtered_df = display_df
                    st.caption(f"Displaying **{len(filtered_df):,}** rows and **{len(filtered_df.columns)}** columns")

                st.dataframe(
                    filtered_df,
                    height=450
                )

                with st.expander(f"Column details & data types ({len(df.columns)} columns)", icon=":material/view_column:"):
                    col_stats = []
                    for col in df.columns:
                        col_stats.append({
                            "Column Name": col,
                            "Data Type": str(df[col].dtype),
                            "Non-Null Count": f"{df[col].notna().sum():,} / {len(df):,}",
                            "Unique Values": df[col].nunique(),
                            "Sample Value": str(df[col].dropna().iloc[0]) if not df[col].dropna().empty else "—"
                        })
                    st.dataframe(pd.DataFrame(col_stats), height=250)

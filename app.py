"""
SocietyScout: point-and-click version.

Start it by double-clicking start_windows.bat (Windows) or start_mac.command (Mac),
or from a terminal in this folder:  streamlit run app.py
"""
import pandas as pd
import streamlit as st

import scraper as ss

st.set_page_config(page_title="SocietyScout", page_icon="🧵", layout="wide")

LABELS = dict(ss.COLUMNS)
KEYS = {label: key for key, label in ss.COLUMNS}
LINK_COLUMNS = {
    "Instagram": st.column_config.LinkColumn("Instagram"),
    "Source page": st.column_config.LinkColumn("Source page"),
}


def rows_to_df(rows):
    return pd.DataFrame(rows, columns=ss.FIELDS).rename(columns=LABELS)


def df_to_rows(df):
    rows = []
    for rec in df.rename(columns=KEYS).to_dict("records"):
        row = {}
        for key in ss.FIELDS:
            value = rec.get(key)
            row[key] = "" if value is None or (not isinstance(value, str) and pd.isna(value)) else str(value).strip()
        if row["org"] or row["society"]:
            rows.append(row)
    return rows


def csv_bytes(rows):
    return ss.to_csv_text(rows).encode("utf-8-sig")


st.session_state.setdefault("editor_version", 0)
IS_CLOUD = ss.running_in_cloud()

# ------------------------------------------------------------------ header --
st.title("SocietyScout")
st.caption("Finds contact details that university societies have posted publicly, and saves them "
           "to CSV. No AI, no subscription.")

if IS_CLOUD:
    with st.expander("Running online: your results are temporary", expanded=False):
        st.warning("This hosted copy loses everything it has saved whenever it restarts, which "
                   "happens after 12 quiet hours and every time the code changes. Download your "
                   "CSV before you finish, and upload it again next time to carry on.")
        uploaded = st.file_uploader("Carry on from a CSV you downloaded earlier", type="csv")
        if uploaded is not None and st.button("Load this file"):
            try:
                added, updated = ss.import_master(uploaded.getvalue().decode("utf-8-sig"))
                st.success(f"Loaded: {added} societies added, {updated} filled in.")
                st.rerun()
            except (ValueError, UnicodeDecodeError) as exc:
                st.error(str(exc))
        if not ss.setting("BRAVE_API_KEY"):
            st.info("Searching by university name is unreliable from a shared server, because "
                    "free search blocks datacentre traffic. Either paste each union's societies "
                    "page into the Search tab, or add a BRAVE_API_KEY in the app's secrets.")

master = ss.read_csv(ss.MASTER_CSV)
orgs = sorted({r["org"] for r in master if r["org"]}, key=str.lower)
st.markdown(f"**{len(master)}** societies saved from **{len(orgs)}** "
            f"{'university or company' if len(orgs) == 1 else 'universities and companies'}.")

tab_search, tab_uk, tab_add, tab_saved = st.tabs(
    ["Search", "All UK universities", "Add by hand", "Saved societies"])

# ------------------------------------------------------------------ search --
with tab_search:
    kind = st.radio("What are you searching?", ["University", "Company or organisation"], horizontal=True)
    name = st.text_input("Name", placeholder="e.g. University of Leeds")
    url = st.text_input(
        "Societies page address (optional, but the most reliable)",
        placeholder="Paste the students' union Societies or Clubs A-Z page here",
        help="Without this, SocietyScout searches the web for the union's societies page. "
             "Pasting the page yourself skips that step and always starts in the right place.",
    )
    if kind != "University":
        st.caption("Company searches look for staff sports clubs, social clubs and networks. "
                   "They're less precise than university searches, so check the results.")
    col1, col2 = st.columns(2)
    max_pages = col1.slider("Pages to check", 20, 500, ss.DEFAULT_MAX_PAGES, step=10,
                            help="About one second per page. Big unions have 300+ societies.")
    only_contacts = col2.checkbox("Only keep societies with contact details")
    deep = st.checkbox(
        "Search deeper for missing contacts", value=True,
        help="For any society with no email on the union page, also check its own website, its "
             "Linktree and web search results. Finds more, but roughly doubles the time.")

    if st.button("Find societies", type="primary", disabled=not name.strip()):
        bar = st.progress(0.0, text="Starting")
        log_box = st.empty()
        log_lines = []

        def on_progress(done, total, msg):
            bar.progress(min(done / max(total, 1), 1.0), text=msg)

        def on_log(msg):
            log_lines.append(msg)
            log_box.caption("  \n".join(log_lines[-4:]))

        try:
            result = ss.run_search(name.strip(), url=url.strip() or None,
                                   company=(kind != "University"), max_pages=max_pages,
                                   only_with_contacts=only_contacts, deep=deep,
                                   progress=on_progress, log=on_log)
        except ValueError as exc:
            st.error(str(exc))
            st.stop()
        bar.progress(1.0, text=f"Done. Checked {result.pages_checked} pages.")
        saved = ss.save_results(result.org, result.rows) if result.rows else {}
        st.session_state["last"] = {"org": result.org, "rows": result.rows,
                                    "saved": saved, "note": result.note}

    last = st.session_state.get("last")
    if last:
        if last["rows"]:
            saved = last["saved"]
            with_email = sum(1 for r in last["rows"] if r["email"])
            st.success(f"Found {len(last['rows'])} societies for {last['org']} ({with_email} with an "
                       f"email): {saved['added']} new, {saved['updated']} updated. "
                       f"Saved to {saved['export']}")
            if saved.get("locked"):
                st.warning("The master CSV is open in another program (probably Excel), so it "
                           f"couldn't be updated. A copy was saved to {saved['locked']}. "
                           "Close the file and search again to merge everything.")
            st.dataframe(rows_to_df(last["rows"]), hide_index=True, column_config=LINK_COLUMNS)
            st.download_button("Download these results", data=csv_bytes(last["rows"]),
                               file_name=f"societies-{ss.slug(last['org'])}-{ss.today()}.csv",
                               mime="text/csv")
            st.caption("Everything was found on public web pages and may be out of date. The Notes "
                       "column says when details came from somewhere other than the union page. "
                       "Check before reaching out.")
        else:
            st.warning(last["note"])

# ------------------------------------------------------ all UK universities --
with tab_uk:
    universities = ss.load_universities()
    if not universities:
        st.error(f"Couldn't find the university list at {ss.UNIVERSITIES_CSV}. "
                 "Keep uk_universities.csv next to scraper.py.")
    else:
        summary = ss.coverage_summary()
        c1, c2, c3 = st.columns(3)
        c1.metric("Universities done", f"{summary['done']} of {summary['total']}")
        c2.metric("Societies collected", summary["societies"])
        c3.metric("Not tried yet", summary["remaining"])
        st.progress(summary["done"] / max(summary["total"], 1))

        st.caption("Works through every UK university with degree-awarding powers. It takes several "
                   "hours, so leave it running. Progress is saved after each university, so you can "
                   "close this and carry on later.")
        c1, c2 = st.columns(2)
        nation = c1.selectbox("Limit to", ["All of the UK", "England", "Scotland", "Wales",
                                           "Northern Ireland"])
        batch = c2.number_input("How many to do in this run", 1, 25 if IS_CLOUD else 200,
                                3 if IS_CLOUD else 10,
                                help="Online, keep this small: a hosted app is cut off if one run "
                                     "takes too long." if IS_CLOUD else
                                     "Start with a few to see how it goes, then raise it.")
        redo = st.checkbox("Include universities already done")
        uk_deep = st.checkbox("Search deeper for missing contacts", value=not IS_CLOUD,
                              key="uk_deep",
                              help="Finds more emails, but roughly doubles how long each "
                                   "university takes.")

        picked = universities if nation == "All of the UK" else \
            [u for u in universities if u.get("nation") == nation]
        coverage = ss.load_coverage()
        todo = [u for u in picked
                if redo or coverage.get(ss.norm(u["name"]), {}).get("status") != "Done"]
        st.caption(f"{len(todo)} to do in this selection.")

        if st.button("Start", type="primary", disabled=not todo):
            bar = st.progress(0.0, text="Starting")
            log_box = st.empty()
            lines = []

            def uk_progress(done, total, msg):
                bar.progress(min(done / max(total, 1), 1.0), text=msg)

            def uk_log(msg):
                lines.append(msg)
                log_box.caption("  \n".join(lines[-6:]))

            done_summary = ss.run_all(todo[:int(batch)], redo=redo, deep=uk_deep,
                                      progress=uk_progress, log=uk_log)
            bar.progress(1.0, text="Finished this run")
            st.success(f"{done_summary['done']} of {done_summary['total']} UK universities done, "
                       f"holding {done_summary['societies']} societies. "
                       f"{done_summary['remaining']} still to try.")
            st.rerun()

        if coverage:
            st.subheader("How each university went")
            cov_df = pd.DataFrame(sorted(coverage.values(), key=lambda r: r["name"].lower()))
            show = st.selectbox("Show", ["Everything", "Done", "No societies page", "Failed"])
            if show != "Everything":
                cov_df = cov_df[cov_df["status"] == show]
            st.dataframe(cov_df, hide_index=True,
                         column_config={"union_url": st.column_config.LinkColumn("Societies page")})
            st.caption("For a university with no societies page found, open its union website, find "
                       "the Societies A-Z page, and paste it into the Search tab. SocietyScout "
                       "remembers it for next time.")


# ------------------------------------------------------------- add by hand --
MANUAL_TEXT_KEYS = ["m_soc", "m_pres", "m_email", "m_phone", "m_insta", "m_site",
                    "m_committee", "m_notes"]


def save_manual():
    s = st.session_state
    ok, msg = ss.add_manual(
        org=s.get("m_org", ""), society=s.get("m_soc", ""), type_=s.get("m_type", "Other"),
        president=s.get("m_pres", ""), email=s.get("m_email", ""), phone=s.get("m_phone", ""),
        instagram=s.get("m_insta", ""), website=s.get("m_site", ""),
        committee=s.get("m_committee", ""), notes=s.get("m_notes", ""),
    )
    s["manual_msg"] = (ok, msg)
    if ok:  # clear the form but keep the university, for adding several in a row
        for key in MANUAL_TEXT_KEYS:
            s[key] = ""


with tab_add:
    with st.form("manual"):
        c1, c2 = st.columns(2)
        c1.text_input("University or company *", key="m_org")
        c2.text_input("Society name *", key="m_soc")
        c1.selectbox("Type", ss.TYPES, key="m_type")
        c2.text_input("President or main contact", key="m_pres")
        c1.text_input("Email", key="m_email")
        c2.text_input("Phone", key="m_phone")
        c1.text_input("Instagram", placeholder="@handle", key="m_insta")
        c2.text_input("Website", key="m_site")
        st.text_area("Committee members", key="m_committee",
                     placeholder="One per line, e.g.\nJane Smith - Treasurer\nTom Reid - Kit Secretary")
        st.text_area("Notes", key="m_notes")
        st.form_submit_button("Save society", type="primary", on_click=save_manual)
    if "manual_msg" in st.session_state:
        ok, msg = st.session_state.pop("manual_msg")
        (st.success if ok else st.error)(msg)

# --------------------------------------------------------- saved societies --
with tab_saved:
    master = ss.read_csv(ss.MASTER_CSV)
    if not master:
        st.info("Nothing saved yet. Search for a university, or add a society by hand.")
    else:
        orgs = sorted({r["org"] for r in master if r["org"]}, key=str.lower)
        pick = st.selectbox("Show", ["All"] + orgs)
        view = master if pick == "All" else [r for r in master if r["org"] == pick]
        st.caption("Edit any cell, set the Status as you work through them, or select rows and "
                   "press Delete. Then press Save changes.")
        edited = st.data_editor(
            rows_to_df(view), hide_index=True, num_rows="dynamic",
            key=f"editor-{pick}-{st.session_state['editor_version']}",
            column_config={
                "Status": st.column_config.SelectboxColumn("Status", options=ss.STATUSES),
                "Type": st.column_config.SelectboxColumn("Type", options=ss.TYPES),
                **LINK_COLUMNS,
            },
        )
        c1, c2 = st.columns([1, 3])
        if c1.button("Save changes", type="primary"):
            edited_rows = df_to_rows(edited)
            new_master = edited_rows if pick == "All" else \
                [r for r in master if r["org"] != pick] + edited_rows
            try:
                ss.write_csv(new_master, ss.MASTER_CSV)
                st.session_state["editor_version"] += 1
                st.session_state["saved_msg"] = "Changes saved."
                st.rerun()
            except ss.FileLockedError as exc:
                st.error("The master CSV is open in another program (probably Excel). Close it and "
                         f"press Save changes again. A copy was saved to {exc.path}")
        c2.download_button("Download as CSV", data=csv_bytes(view),
                           file_name=f"societies-{ss.slug(pick)}-{ss.today()}.csv", mime="text/csv")
        if "saved_msg" in st.session_state:
            st.success(st.session_state.pop("saved_msg"))
        if IS_CLOUD:
            st.caption("Download this before you finish. The hosted copy is wiped when the app "
                       "restarts.")
        else:
            st.caption(f"Master file: {ss.MASTER_CSV}")

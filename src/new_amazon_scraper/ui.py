"""Streamlit UI.

Thin shell over `ScraperAPIClient`. No DB, no OpenAI, no scraping logic — if
the API is down, this page is useless, and that's the point: one source of
truth for behaviour, one process to debug when it misbehaves.

Run:  uv run streamlit run src/new_amazon_scraper/ui.py
"""

import asyncio
import os
from typing import Any

import streamlit as st

from new_amazon_scraper.ui_client import APIError, ScraperAPIClient

DEFAULT_API_URL = os.getenv("SCRAPER_API_URL", "http://localhost:8000")


def _run(coro):
    """Streamlit reruns the whole script each interaction; an asyncio.run per
    call is the simplest bridge. Don't try to cache a loop here — Streamlit's
    threading model makes it more trouble than it's worth.
    """
    return asyncio.run(coro)


def _client(base_url: str) -> ScraperAPIClient:
    return ScraperAPIClient(base_url=base_url)


async def _call(base_url: str, fn_name: str, *args, **kwargs) -> Any:
    async with _client(base_url) as c:
        return await getattr(c, fn_name)(*args, **kwargs)


def _show_api_error(e: APIError) -> None:
    st.error(f"API returned {e.status}: {e.detail}")


# --- Page config ------------------------------------------------------------

st.set_page_config(page_title="Amazon Scraper", page_icon=":package:", layout="wide")

with st.sidebar:
    st.header("Settings")
    api_url = st.text_input("API base URL", DEFAULT_API_URL)
    if st.button("Check API"):
        try:
            health = _run(_call(api_url, "health"))
            st.success(f"API up: {health}")
        except Exception as e:  # noqa: BLE001
            st.error(f"API unreachable: {e}")

st.title("Amazon Scraper")

tab_search, tab_detail, tab_scrape, tab_ask = st.tabs(
    ["Search", "Product detail", "Scrape new", "Ask agent"]
)


# --- Search tab -------------------------------------------------------------

with tab_search:
    query = st.text_input("Search products", placeholder="echo dot, sony headphones, ...")
    limit = st.slider("Max results", 1, 50, 10)
    if query:
        try:
            hits = _run(_call(api_url, "search", query, limit=limit))
        except APIError as e:
            _show_api_error(e)
        else:
            if not hits:
                st.info("No matches.")
            else:
                st.dataframe(hits, use_container_width=True)


# --- Product detail tab -----------------------------------------------------

with tab_detail:
    col_asin, col_cc, col_btn = st.columns([3, 1, 1])
    asin = col_asin.text_input("ASIN", key="detail_asin", placeholder="B08N5WRWNW")
    country = col_cc.text_input("Country", key="detail_cc", value="US", max_chars=2)
    fetch = col_btn.button("Load", use_container_width=True)

    if fetch and asin:
        try:
            product = _run(_call(api_url, "get_product", asin, country))
        except APIError as e:
            _show_api_error(e)
        else:
            if product is None:
                st.warning("No product stored for that ASIN/country. Try the Scrape tab.")
            else:
                st.subheader(product.get("title") or "(no title)")
                meta_left, meta_right = st.columns(2)
                price = product.get("price") or "—"
                currency = product.get("currency") or ""
                with meta_left:
                    st.metric("Price", f"{price} {currency}")
                    st.metric("Rating", product.get("rating") or "—")
                with meta_right:
                    st.metric("Brand", product.get("brand") or "—")
                    st.metric("Reviews", product.get("review_count") or "—")

                try:
                    history = _run(_call(api_url, "price_history", asin, country))
                except APIError as e:
                    _show_api_error(e)
                else:
                    if history:
                        st.subheader("Price history")
                        st.dataframe(history, use_container_width=True)


# --- Scrape tab -------------------------------------------------------------

with tab_scrape:
    st.write(
        "Submit an ASIN to fetch and store. Runs in the background — "
        "refresh after a few seconds."
    )
    new_asin = st.text_input("ASIN", key="scrape_asin", placeholder="B08N5WRWNW")
    new_cc = st.text_input("Country code", key="scrape_cc", value="US", max_chars=2)
    if st.button("Scrape"):
        if not new_asin:
            st.warning("Enter an ASIN.")
        else:
            try:
                result = _run(_call(api_url, "scrape", new_asin, new_cc))
            except APIError as e:
                _show_api_error(e)
            else:
                st.success(f"Queued: {result}")


# --- Ask tab ----------------------------------------------------------------

with tab_ask:
    question = st.text_area("Ask the agent", placeholder="What's the price of the Echo Dot?")
    if st.button("Ask"):
        if not question.strip():
            st.warning("Enter a question.")
        else:
            with st.spinner("Thinking..."):
                try:
                    answer = _run(_call(api_url, "ask", question))
                except APIError as e:
                    _show_api_error(e)
                else:
                    st.markdown(answer)

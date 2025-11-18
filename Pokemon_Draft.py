import streamlit as st
import pandas as pd
from io import BytesIO
import re
import requests

st.set_page_config(page_title="Pokémon Auction Draft", layout="wide")

# ---------- Helpers ----------

def init_state():
    if "draft_started" not in st.session_state:
        st.session_state.draft_started = False
        st.session_state.players = []
        st.session_state.starting_budget = 1000
        st.session_state.max_slots = 6
        st.session_state.budgets = {}
        st.session_state.rosters = {}
        st.session_state.current_nominator_index = 0
        st.session_state.current_pokemon = None
        st.session_state.current_bid = None
        st.session_state.current_bidder = None
        st.session_state.log = []
        st.session_state.draft_finished = False
        st.session_state.pokemon_pool = []  # filled from PokeAPI


def pokemon_slug(name: str) -> str:
    """
    Convert a Pokémon name to a Pokemondb sprite slug.
    We assume names like 'charizard', 'mr-mime', 'tapu-koko', etc.
    """
    slug = name.strip().lower()
    slug = slug.replace("♀", "-f").replace("♂", "-m")
    slug = re.sub(r"[.']", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    return slug


def pokemon_image_url(name: str) -> str:
    slug = pokemon_slug(name)
    return f"https://img.pokemondb.net/sprites/home/normal/{slug}.png"


def fetch_pokemon_from_api() -> list:
    """
    Fetch Pokémon names from PokeAPI.
    Returns a list of names like 'pikachu', 'charizard', 'mr-mime', etc.
    """
    url = "https://pokeapi.co/api/v2/pokemon?limit=2000"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        st.warning(
            "Couldn't load Pokémon names from PokeAPI. "
            f"Reason: {e}. Autocomplete will be disabled."
        )
        return []

    results = data.get("results", [])
    names = []
    for entry in results:
        n = entry.get("name", "").strip()
        if n:
            names.append(n)

    if not names:
        st.warning(
            "PokeAPI returned no Pokémon names. "
            "Autocomplete will be disabled."
        )
    return names


def advance_nominator():
    """Advance nominator to next player who can still participate."""
    players = st.session_state.players
    max_slots = st.session_state.max_slots
    budgets = st.session_state.budgets
    rosters = st.session_state.rosters

    for _ in range(len(players)):
        st.session_state.current_nominator_index = (
            st.session_state.current_nominator_index + 1
        ) % len(players)
        p = players[st.session_state.current_nominator_index]
        if len(rosters[p]) < max_slots and budgets[p] >= 50:
            return

    # If we get here, no one can nominate anymore -> draft finished
    st.session_state.draft_finished = True


def everyone_full():
    max_slots = st.session_state.max_slots
    rosters = st.session_state.rosters
    return all(len(rosters[p]) >= max_slots for p in st.session_state.players)


# ---------- Initialize ----------

init_state()

st.title("Pokémon Auction Draft")

# ---------- Sidebar: Setup ----------

st.sidebar.header("Setup")

if not st.session_state.draft_started:
    with st.sidebar.form("setup_form"):
        player_names_text = st.text_area(
            "Player names (one per line):",
            value="Player 1\nPlayer 2\nPlayer 3\nPlayer 4",
            height=120,
        )

        starting_budget = st.number_input(
            "Starting budget", value=1000, min_value=100, step=50
        )
        max_slots = st.number_input(
            "Max Pokémon per player", value=6, min_value=1, max_value=12, step=1
        )
        start_button = st.form_submit_button("Start Draft")

    if start_button:
        players = [p.strip() for p in player_names_text.splitlines() if p.strip()]
        if len(players) < 2:
            st.sidebar.error("You need at least 2 players.")
        else:
            # Load Pokémon names from PokeAPI ONCE when starting the draft
            with st.spinner("Loading Pokémon names from PokeAPI..."):
                pokemon_pool = fetch_pokemon_from_api()

            st.session_state.players = players
            st.session_state.starting_budget = starting_budget
            st.session_state.max_slots = max_slots
            st.session_state.budgets = {p: starting_budget for p in players}
            st.session_state.rosters = {p: [] for p in players}
            st.session_state.current_nominator_index = 0
            st.session_state.current_pokemon = None
            st.session_state.current_bid = None
            st.session_state.current_bidder = None
            st.session_state.log = []
            st.session_state.draft_started = True
            st.session_state.draft_finished = False
            st.session_state.pokemon_pool = pokemon_pool
else:
    st.sidebar.success("Draft is in progress.")
    if st.sidebar.button("Reset Draft"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        init_state()
        st.rerun()

# ---------- Main UI ----------

if not st.session_state.draft_started:
    st.markdown(
        """
        ### How it works
        
        1. Enter player names on the left, then click **Start Draft**.  
        2. The app will:
           - Pull **Pokémon names from PokeAPI** for autocomplete.  
           - Cycle nominators in order.  
           - Auto-place a **$50 opening bid** from the nominator.  
           - Let you raise bids in **$25 increments** and close the auction.  
           - Track budgets and show each player's party with images.  
        3. When you're done, click **Download Excel** for a full draft sheet.
        """
    )
else:
    players = st.session_state.players
    budgets = st.session_state.budgets
    rosters = st.session_state.rosters
    max_slots = st.session_state.max_slots

    # Top summary
    st.subheader("Draft Status")

    col_summary, col_excel = st.columns([3, 1])

    with col_summary:
        df_status = pd.DataFrame(
            {
                "Player": players,
                "Remaining $": [budgets[p] for p in players],
                "Slots used": [len(rosters[p]) for p in players],
                "Slots max": [max_slots] * len(players),
            }
        )
        st.dataframe(df_status, hide_index=True, use_container_width=True)

    with col_excel:
        st.markdown("### Export")

        def build_excel_data():
            rows = []
            for p in players:
                row = {
                    "Player": p,
                    "RemainingBudget": budgets[p],
                }
                for i in range(max_slots):
                    if i < len(rosters[p]):
                        mon = rosters[p][i]
                        row[f"Slot{i+1}_Pokemon"] = mon["name"]
                        row[f"Slot{i+1}_Price"] = mon["price"]
                    else:
                        row[f"Slot{i+1}_Pokemon"] = ""
                        row[f"Slot{i+1}_Price"] = ""
                rows.append(row)
            return pd.DataFrame(rows)

        excel_df = build_excel_data()
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            excel_df.to_excel(writer, index=False, sheet_name="Draft")
        buffer.seek(0)

        st.download_button(
            label="Download Excel",
            data=buffer,
            file_name="draft_results.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    st.markdown("---")

    col_left, col_right = st.columns([2, 3])

    # ---------- Left: Nomination & Bidding ----------
    with col_left:
        st.subheader("Nomination & Bidding")

        if st.session_state.draft_finished or everyone_full():
            st.success("Draft finished! Everyone is full or cannot nominate anymore.")
        else:
            current_nominator = players[st.session_state.current_nominator_index]

            if st.session_state.current_pokemon is None:
                st.markdown(f"**Current nominator:** {current_nominator}")

                if len(rosters[current_nominator]) >= max_slots:
                    st.info(
                        f"{current_nominator} has a full team. Advancing nominator..."
                    )
                    advance_nominator()
                    st.rerun()

                elif budgets[current_nominator] < 50:
                    st.info(
                        f"{current_nominator} doesn't have enough money to nominate "
                        "($50 needed). Advancing nominator..."
                    )
                    advance_nominator()
                    st.rerun()
                else:
                    pokemon_pool = st.session_state.pokemon_pool

                    if pokemon_pool:
                        # Filter out already drafted mons from the pool
                        drafted_mons = {
                            mon["name"]
                            for mons in rosters.values()
                            for mon in mons
                        }
                        available_mons = [
                            m for m in pokemon_pool if m not in drafted_mons
                        ]

                        if not available_mons:
                            st.info("All Pokémon in the pool have been drafted.")
                            nominated_mon = ""
                        else:
                            nominated_mon = st.selectbox(
                                "Nominate a Pokémon (type to search):",
                                options=sorted(available_mons),
                                key="nominate_select",
                            )
                    else:
                        # Fallback to manual input if API failed
                        nominated_mon = st.text_input(
                            "Nominate a Pokémon (text, e.g. 'landorus-therian', 'archaludon'):",
                            key="nominate_input",
                        )

                    if st.button("Nominate", type="primary"):
                        mon_name = nominated_mon.strip() if nominated_mon else ""
                        if not mon_name:
                            st.error("Select or enter a Pokémon name before nominating.")
                        else:
                            st.session_state.current_pokemon = mon_name
                            opening_bid = min(50, budgets[current_nominator])
                            st.session_state.current_bid = opening_bid
                            st.session_state.current_bidder = current_nominator
                            st.session_state.log.append(
                                f"{current_nominator} nominated {mon_name} "
                                f"with opening bid ${opening_bid}."
                            )
                            st.rerun()
            else:
                mon_name = st.session_state.current_pokemon
                current_bid = st.session_state.current_bid
                current_bidder = st.session_state.current_bidder

                st.markdown(f"### Auction: **{mon_name}**")
                st.markdown(
                    f"Current bid: **${current_bid}** by **{current_bidder}**"
                )

                img_url = pokemon_image_url(mon_name)
                st.image(img_url, width=128, caption=mon_name)

                bidder = st.selectbox(
                    "Bidder",
                    options=players,
                    index=players.index(current_bidder)
                    if current_bidder in players
                    else 0,
                    key="bidder_select",
                )

                max_allowed = budgets[bidder]
                min_bid = current_bid + 25  # $25 increments

                if max_allowed < min_bid:
                    st.info(
                        f"{bidder} does not have enough money to outbid the current bid "
                        f"(needs at least ${min_bid}, has ${max_allowed})."
                    )
                else:
                    allowed_bids = list(range(min_bid, max_allowed + 1, 25))

                    new_bid = st.selectbox(
                        f"New bid for {bidder} (increments of $25)",
                        options=allowed_bids,
                        key="bid_amount",
                    )

                    if st.button("Place Bid"):
                        st.session_state.current_bid = int(new_bid)
                        st.session_state.current_bidder = bidder
                        st.session_state.log.append(
                            f"{bidder} bids ${new_bid} on {mon_name}."
                        )
                        st.rerun()

                if st.button("Close bidding & assign Pokémon", type="primary"):
                    winner = st.session_state.current_bidder
                    price = st.session_state.current_bid

                    if budgets[winner] < price:
                        st.error(
                            "Error: winner does not have enough budget "
                            "(something went wrong)."
                        )
                    elif len(rosters[winner]) >= max_slots:
                        st.error("Error: winner already has a full team.")
                    else:
                        budgets[winner] -= price
                        rosters[winner].append({"name": mon_name, "price": price})
                        st.session_state.log.append(
                            f"{mon_name} goes to {winner} for ${price}."
                        )
                        # clear current auction
                        st.session_state.current_pokemon = None
                        st.session_state.current_bid = None
                        st.session_state.current_bidder = None

                        if everyone_full():
                            st.session_state.draft_finished = True
                        else:
                            advance_nominator()

                        st.rerun()

        st.markdown("### Log")
        for entry in reversed(st.session_state.log[-15:]):
            st.write("- " + entry)

    # ---------- Right: Parties with graphics ----------
    with col_right:
        st.subheader("Current Parties")

        for p in players:
            st.markdown(
                f"#### {p} – ${budgets[p]} left "
                f"({len(rosters[p])}/{max_slots})"
            )
            mons = rosters[p]
            if not mons:
                st.write("_No Pokémon yet._")
            else:
                cols = st.columns(max_slots)
                for i in range(max_slots):
                    with cols[i]:
                        if i < len(mons):
                            mon = mons[i]
                            st.image(
                                pokemon_image_url(mon["name"]),
                                width=80,
                            )
                            st.caption(f"{mon['name']}\n${mon['price']}")
                        else:
                            st.write("Empty")
            st.markdown("---")

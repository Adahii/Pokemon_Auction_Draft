import streamlit as st
import pandas as pd
from io import BytesIO
import re
import requests
import random
import string

st.set_page_config(page_title="Pokémon Auction Draft", layout="wide")

# ---------- Global game store (in-memory, per server) ----------

# code -> game dict
GAMES = {}


# ---------- Pokémon helpers ----------

def pokemon_slug(name: str) -> str:
    """Fallback slug function if ID lookup fails."""
    slug = name.strip().lower()
    slug = slug.replace("♀", "-f").replace("♂", "-m")
    slug = re.sub(r"[.']", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    return slug


@st.cache_data(show_spinner=False)
def fetch_pokemon_from_api():
    """
    Fetch Pokémon names + numeric IDs from PokeAPI.
    Returns (names_list, name_to_id_dict).
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
        return [], {}

    results = data.get("results", [])
    names = []
    name_to_id = {}

    for entry in results:
        n = entry.get("name", "").strip()
        url = entry.get("url", "").strip()
        if not n or not url:
            continue

        # URL looks like .../pokemon/25/
        try:
            poke_id = int(url.rstrip("/").split("/")[-1])
        except ValueError:
            continue

        names.append(n)
        name_to_id[n.lower()] = poke_id

    if not names:
        st.warning(
            "PokeAPI returned no Pokémon names. "
            "Autocomplete will be disabled."
        )

    return names, name_to_id


POKEMON_POOL, POKEMON_ID_MAP = fetch_pokemon_from_api()


def pokemon_image_url(name: str) -> str:
    """
    Primary: use PokeAPI numeric ID -> GitHub sprites.
    Fallback: try Pokemondb slug (for any weird manual entries).
    """
    name_key = name.strip().lower()
    poke_id = POKEMON_ID_MAP.get(name_key)

    if poke_id:
        # PokeAPI sprite set (safe for programmatic use)
        return f"https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{poke_id}.png"

    # Fallback: slug-based URL (may or may not work)
    slug = pokemon_slug(name)
    return f"https://img.pokemondb.net/sprites/home/normal/{slug}.png"


# ---------- Game helpers ----------

def generate_game_code(length: int = 6) -> str:
    chars = string.ascii_uppercase + string.digits
    while True:
        code = "".join(random.choice(chars) for _ in range(length))
        if code not in GAMES:
            return code


def create_game(players, starting_budget: int, max_slots: int):
    budgets = {p: starting_budget for p in players}
    rosters = {p: [] for p in players}
    return {
        "players": players,
        "starting_budget": starting_budget,
        "max_slots": max_slots,
        "budgets": budgets,
        "rosters": rosters,
        "current_nominator_index": 0,
        "current_pokemon": None,
        "current_bid": None,
        "current_bidder": None,
        "log": [],
        "draft_finished": False,
    }


def advance_nominator(game):
    """Advance nominator to next player who can still participate."""
    players = game["players"]
    max_slots = game["max_slots"]
    budgets = game["budgets"]
    rosters = game["rosters"]

    for _ in range(len(players)):
        game["current_nominator_index"] = (
            game["current_nominator_index"] + 1
        ) % len(players)
        p = players[game["current_nominator_index"]]
        if len(rosters[p]) < max_slots and budgets[p] >= 50:
            return

    # If we get here, no one can nominate anymore -> draft finished
    game["draft_finished"] = True


def everyone_full(game) -> bool:
    max_slots = game["max_slots"]
    rosters = game["rosters"]
    return all(len(rosters[p]) >= max_slots for p in game["players"])


# ---------- Session-level state (per browser) ----------

if "game_code" not in st.session_state:
    st.session_state.game_code = None  # which game this user is attached to
if "is_host" not in st.session_state:
    st.session_state.is_host = False   # host or viewer


# ---------- UI: landing (Host / Join) ----------

def show_landing_page():
    st.title("Pokémon Auction Draft")

    st.markdown(
        "Create a game as **host** (gets control of the draft), "
        "or **join** an existing game with a code to watch live."
    )

    col_host, col_join = st.columns(2)

    with col_host:
        st.subheader("Host a Game")

        with st.form("host_form"):
            player_names_text = st.text_area(
                "Player names (one per line):",
                value="Player 1\nPlayer 2\nPlayer 3\nPlayer 4",
                height=120,
            )
            starting_budget = st.number_input(
                "Starting budget", value=1000, min_value=100, step=50
            )
            max_slots = st.number_input(
                "Max Pokémon per player",
                value=6,
                min_value=1,
                max_value=12,
                step=1,
            )
            host_submit = st.form_submit_button("Create Game")

        if host_submit:
            players = [p.strip() for p in player_names_text.splitlines() if p.strip()]
            if len(players) < 2:
                st.error("You need at least 2 players.")
            else:
                code = generate_game_code()
                GAMES[code] = create_game(players, starting_budget, max_slots)

                st.session_state.game_code = code
                st.session_state.is_host = True

                st.success(f"Game created! Code: **{code}**")
                st.info("Share this code with players so they can join as viewers.")
                st.rerun()  # jump into game view

    with col_join:
        st.subheader("Join a Game")

        with st.form("join_form"):
            join_code = st.text_input("Enter game code (e.g. A7Q2KD):").upper().strip()
            join_submit = st.form_submit_button("Join Game")

        if join_submit:
            if not join_code:
                st.error("Enter a game code.")
            elif join_code not in GAMES:
                st.error("No game found with that code. Check the code and try again.")
            else:
                st.session_state.game_code = join_code
                st.session_state.is_host = False
                st.success(f"Joined game **{join_code}** as viewer.")
                st.rerun()


# ---------- UI: single game view ----------

def show_game_page(game_code: str, is_host: bool):
    game = GAMES.get(game_code)

    st.title(f"Game Code: {game_code}")
    role = "Host (controls draft)" if is_host else "Viewer (read-only)"
    st.caption(f"Role: **{role}**")

    if game is None:
        st.error(
            "This game no longer exists (server may have restarted or code is invalid)."
        )
        if st.button("Back to Home"):
            st.session_state.game_code = None
            st.session_state.is_host = False
            st.rerun()
        return

    if st.button("Leave Game"):
        st.session_state.game_code = None
        st.session_state.is_host = False
        st.rerun()

    st.markdown("---")

    players = game["players"]
    budgets = game["budgets"]
    rosters = game["rosters"]
    max_slots = game["max_slots"]

    # ---------- Top: status + export ----------

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
        st.dataframe(
            df_status,
            hide_index=True,
            width="stretch",
        )

    with col_excel:
        st.markdown("### Export")

        def build_excel_data(game_obj):
            rows = []
            for p in game_obj["players"]:
                row = {
                    "Player": p,
                    "RemainingBudget": game_obj["budgets"][p],
                }
                max_slots_local = game_obj["max_slots"]
                for i in range(max_slots_local):
                    if i < len(game_obj["rosters"][p]):
                        mon = game_obj["rosters"][p][i]
                        row[f"Slot{i+1}_Pokemon"] = mon["name"]
                        row[f"Slot{i+1}_Price"] = mon["price"]
                    else:
                        row[f"Slot{i+1}_Pokemon"] = ""
                        row[f"Slot{i+1}_Price"] = ""
                rows.append(row)
            return pd.DataFrame(rows)

        excel_df = build_excel_data(game)
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            excel_df.to_excel(writer, index=False, sheet_name="Draft")
        buffer.seek(0)

        st.download_button(
            label="Download Excel",
            data=buffer,
            file_name=f"draft_results_{game_code}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    st.markdown("---")

    col_left, col_right = st.columns([2, 3])

    # ---------- Left: Nomination & Bidding ----------
    with col_left:
        st.subheader("Nomination & Bidding")

        if game["draft_finished"] or everyone_full(game):
            st.success("Draft finished! Everyone is full or cannot nominate anymore.")
        else:
            current_nominator = players[game["current_nominator_index"]]

            if not is_host:
                # VIEWER MODE (read-only)
                st.info(f"Host is controlling this draft.\n\nCurrent nominator: **{current_nominator}**")

                if game["current_pokemon"] is None:
                    st.write("Waiting for host to nominate a Pokémon...")
                else:
                    mon_name = game["current_pokemon"]
                    current_bid = game["current_bid"]
                    current_bidder = game["current_bidder"]

                    st.markdown(f"### Auction: **{mon_name}**")
                    st.markdown(
                        f"Current bid: **${current_bid}** by **{current_bidder}**"
                    )
                    st.image(
                        pokemon_image_url(mon_name),
                        width=128,
                        caption=mon_name,
                    )

            else:
                # HOST MODE (full control)
                if game["current_pokemon"] is None:
                    st.markdown(f"**Current nominator:** {current_nominator}")

                    if len(rosters[current_nominator]) >= max_slots:
                        st.info(
                            f"{current_nominator} has a full team. Advancing nominator..."
                        )
                        advance_nominator(game)
                        st.rerun()

                    elif budgets[current_nominator] < 50:
                        st.info(
                            f"{current_nominator} doesn't have enough money to nominate "
                            "($50 needed). Advancing nominator..."
                        )
                        advance_nominator(game)
                        st.rerun()
                    else:
                        # Nomination with autocomplete from full Pokémon pool
                        if POKEMON_POOL:
                            drafted_mons = {
                                mon["name"]
                                for mons in rosters.values()
                                for mon in mons
                            }
                            available_mons = [
                                m for m in POKEMON_POOL if m not in drafted_mons
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
                            nominated_mon = st.text_input(
                                "Nominate a Pokémon (text, e.g. 'landorus-therian', 'archaludon'):",
                                key="nominate_input",
                            )

                        if st.button("Nominate", type="primary"):
                            mon_name = nominated_mon.strip() if nominated_mon else ""
                            if not mon_name:
                                st.error(
                                    "Select or enter a Pokémon name before nominating."
                                )
                            else:
                                game["current_pokemon"] = mon_name
                                opening_bid = min(50, budgets[current_nominator])
                                game["current_bid"] = opening_bid
                                game["current_bidder"] = current_nominator
                                game["log"].append(
                                    f"{current_nominator} nominated {mon_name} "
                                    f"with opening bid ${opening_bid}."
                                )
                                st.rerun()
                else:
                    mon_name = game["current_pokemon"]
                    current_bid = game["current_bid"]
                    current_bidder = game["current_bidder"]

                    st.markdown(f"### Auction: **{mon_name}**")
                    st.markdown(
                        f"Current bid: **${current_bid}** by **{current_bidder}**"
                    )

                    st.image(
                        pokemon_image_url(mon_name),
                        width=128,
                        caption=mon_name,
                    )

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
                            game["current_bid"] = int(new_bid)
                            game["current_bidder"] = bidder
                            game["log"].append(
                                f"{bidder} bids ${new_bid} on {mon_name}."
                            )
                            st.rerun()

                    if st.button("Close bidding & assign Pokémon", type="primary"):
                        winner = game["current_bidder"]
                        price = game["current_bid"]

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
                            game["log"].append(
                                f"{mon_name} goes to {winner} for ${price}."
                            )
                            game["current_pokemon"] = None
                            game["current_bid"] = None
                            game["current_bidder"] = None

                            if everyone_full(game):
                                game["draft_finished"] = True
                            else:
                                advance_nominator(game)

                            st.rerun()

        st.markdown("### Log")
        for entry in reversed(game["log"][-15:]):
            st.write("- " + entry)

    # ---------- Right: Parties ----------
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


# ---------- Main app routing ----------

if st.session_state.game_code is None:
    show_landing_page()
else:
    show_game_page(st.session_state.game_code, st.session_state.is_host)

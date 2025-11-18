import streamlit as st
import pandas as pd
from io import BytesIO
import re
import requests
import random
import string

st.set_page_config(page_title="Pokémon Auction Draft", layout="wide")

# ---------- Shared game store (persists across reruns & sessions) ----------

@st.cache_resource
def get_game_store():
    """
    Returns a shared dict: code -> game_state.
    This persists across script reruns and is shared by all users
    on the same Streamlit server.
    """
    return {}  # we will mutate this, NOT reassign


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

PLAYER_ICONS = [
    "🔥", "💧", "🌿", "⚡", "🪨",
    "❄️", "🌪️", "🌙", "☀️", "⭐",
    "🐉", "🦊", "🐢", "🦅", "🐍",
    "🦁", "🐧", "🦈", "🦖", "🐸",
]


def generate_game_code(length: int = 6) -> str:
    chars = string.ascii_uppercase + string.digits
    games = get_game_store()
    while True:
        code = "".join(random.choice(chars) for _ in range(length))
        if code not in games:
            return code


def create_game(starting_budget: int, max_slots: int):
    """
    Create a new game in 'lobby' status.
    Players will join via lobby; host does NOT add player names.
    """
    return {
        "status": "lobby",           # 'lobby' -> 'draft' -> 'finished'
        "starting_budget": starting_budget,
        "max_slots": max_slots,

        "lobby_players": {},         # name -> icon

        "players": [],               # frozen at draft start
        "player_icons": {},          # name -> icon
        "budgets": {},               # name -> $
        "rosters": {},               # name -> list of mons

        "current_nominator_index": 0,
        "current_pokemon": None,
        "current_bid": None,
        "current_bidder": None,
        "log": [],
        "draft_finished": False,
    }


def start_draft(game) -> bool:
    """
    Move from lobby to draft.
    Returns True if started, False if not enough players.
    """
    lobby_players = game["lobby_players"]
    if len(lobby_players) < 2:
        return False

    players = list(lobby_players.keys())
    game["players"] = players
    game["player_icons"] = dict(lobby_players)
    game["budgets"] = {p: game["starting_budget"] for p in players}
    game["rosters"] = {p: [] for p in players}
    game["current_nominator_index"] = 0
    game["current_pokemon"] = None
    game["current_bid"] = None
    game["current_bidder"] = None
    game["log"] = []
    game["draft_finished"] = False
    game["status"] = "draft"
    return True


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
if "player_name" not in st.session_state:
    st.session_state.player_name = None
if "player_icon" not in st.session_state:
    st.session_state.player_icon = None


# ---------- UI: landing (Host / Join) ----------

def show_landing_page():
    st.title("Pokémon Auction Draft")

    st.markdown(
        "Create a game as **host** (gets control of the draft), "
        "or **join** an existing game with a code to sit in the **lobby** "
        "and choose your name + icon."
    )

    col_host, col_join = st.columns(2)

    # ----- Host -----
    with col_host:
        st.subheader("Host a Game")

        with st.form("host_form"):
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
            code = generate_game_code()
            games = get_game_store()
            games[code] = create_game(starting_budget, max_slots)

            st.session_state.game_code = code
            st.session_state.is_host = True
            st.session_state.player_name = None
            st.session_state.player_icon = None

            st.success(f"Game created! Code: **{code}**")
            st.info("Share this code with players so they can join the lobby.")
            st.rerun()  # jump into game view

    # ----- Join -----
    with col_join:
        st.subheader("Join a Game")

        with st.form("join_form"):
            join_code = st.text_input("Enter game code (e.g. A7Q2KD):").upper().strip()
            player_name = st.text_input("Your display name:")
            icon = st.selectbox("Choose an icon:", PLAYER_ICONS, index=0)
            join_submit = st.form_submit_button("Join Lobby")

        if join_submit:
            if not join_code:
                st.error("Enter a game code.")
                return

            games = get_game_store()
            if join_code not in games:
                st.error("No game found with that code. Check the code and try again.")
                return

            game = games[join_code]

            # If draft already started, join as viewer (no lobby registration)
            if game["status"] != "lobby":
                st.session_state.game_code = join_code
                st.session_state.is_host = False
                st.session_state.player_name = None
                st.session_state.player_icon = None
                st.success(f"Joined game **{join_code}** as viewer.")
                st.rerun()
                return

            # Lobby join (player)
            if not player_name.strip():
                st.error("Enter a display name to join as a player.")
                return

            if player_name in game["lobby_players"]:
                st.error("That name is already taken in this lobby. Choose another.")
                return

            game["lobby_players"][player_name] = icon
            st.session_state.game_code = join_code
            st.session_state.is_host = False
            st.session_state.player_name = player_name
            st.session_state.player_icon = icon

            st.success(f"Joined lobby for **{join_code}** as {icon} **{player_name}**.")
            st.rerun()


# ---------- UI: Lobby ----------

def show_lobby_view(game_code: str, is_host: bool, game: dict):
    st.subheader("Lobby")

    st.write(
        f"Starting budget: **${game['starting_budget']}**  |  "
        f"Max Pokémon per player: **{game['max_slots']}**"
    )

    lobby_players = game["lobby_players"]

    if not lobby_players:
        st.write("_No players have joined yet._")
    else:
        st.markdown("### Players in Lobby")
        # Show in a grid
        names = list(lobby_players.keys())
        icons = [lobby_players[n] for n in names]
        cols = st.columns(4)
        for idx, (n, icon) in enumerate(zip(names, icons)):
            with cols[idx % 4]:
                st.markdown(f"{icon} **{n}**")

    # Info for this client
    if not is_host and st.session_state.player_name:
        st.info(
            f"You are {st.session_state.player_icon} **{st.session_state.player_name}** "
            f"in this lobby. Waiting for host to start the draft."
        )
    elif not is_host:
        st.info("You joined as a viewer. Waiting for host to start the draft.")

    # Host controls
    if is_host:
        st.markdown("### Host Controls")

        if st.button("Start Draft with Current Players"):
            ok = start_draft(game)
            if not ok:
                st.error("You need at least 2 players in the lobby to start.")
            else:
                st.success("Draft started!")
                st.rerun()


# ---------- UI: Draft ----------

def show_draft_view(game_code: str, is_host: bool, game: dict):
    players = game["players"]
    budgets = game["budgets"]
    rosters = game["rosters"]
    max_slots = game["max_slots"]
    player_icons = game["player_icons"]

    # ---------- Top: status + export ----------

    st.subheader("Draft Status")

    col_summary, col_excel = st.columns([3, 1])

    with col_summary:
        df_status = pd.DataFrame(
            {
                "Player": players,
                "Icon": [player_icons.get(p, "") for p in players],
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
                    "Icon": game_obj["player_icons"].get(p, ""),
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
            current_nominator_icon = player_icons.get(current_nominator, "")

            if not is_host:
                # VIEWER MODE (read-only)
                st.info(
                    "Host is controlling this draft.\n\n"
                    f"Current nominator: {current_nominator_icon} **{current_nominator}**"
                )

                if game["current_pokemon"] is None:
                    st.write("Waiting for host to nominate a Pokémon...")
                else:
                    mon_name = game["current_pokemon"]
                    current_bid = game["current_bid"]
                    current_bidder = game["current_bidder"]
                    current_bidder_icon = player_icons.get(current_bidder, "")

                    st.markdown(f"### Auction: **{mon_name}**")
                    st.markdown(
                        f"Current bid: **${current_bid}** by "
                        f"{current_bidder_icon} **{current_bidder}**"
                    )
                    st.image(
                        pokemon_image_url(mon_name),
                        width=128,
                        caption=mon_name,
                    )

            else:
                # HOST MODE (full control)
                if game["current_pokemon"] is None:
                    st.markdown(
                        f"**Current nominator:** "
                        f"{current_nominator_icon} **{current_nominator}**"
                    )

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
                                    f"{current_nominator_icon} {current_nominator} "
                                    f"nominated {mon_name} with opening bid ${opening_bid}."
                                )
                                st.rerun()
                else:
                    mon_name = game["current_pokemon"]
                    current_bid = game["current_bid"]
                    current_bidder = game["current_bidder"]
                    current_bidder_icon = player_icons.get(current_bidder, "")

                    st.markdown(f"### Auction: **{mon_name}**")
                    st.markdown(
                        f"Current bid: **${current_bid}** by "
                        f"{current_bidder_icon} **{current_bidder}**"
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
                                f"{game['player_icons'].get(bidder, '')} {bidder} "
                                f"bids ${new_bid} on {mon_name}."
                            )
                            st.rerun()

                    if st.button("Close bidding & assign Pokémon", type="primary"):
                        winner = game["current_bidder"]
                        price = game["current_bid"]
                        winner_icon = player_icons.get(winner, "")

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
                                f"{mon_name} goes to {winner_icon} {winner} "
                                f"for ${price}."
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
            icon = player_icons.get(p, "")
            st.markdown(
                f"#### {icon} {p} – ${budgets[p]} left "
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


# ---------- UI: single game wrapper ----------

def show_game_page(game_code: str, is_host: bool):
    games = get_game_store()
    game = games.get(game_code)

    st.title(f"Game Code: {game_code}")
    role = "Host (controls draft)" if is_host else "Player/Viewer"
    st.caption(f"Role: **{role}**")

    if game is None:
        st.error(
            "This game no longer exists (server may have restarted or code is invalid)."
        )
        if st.button("Back to Home"):
            st.session_state.game_code = None
            st.session_state.is_host = False
            st.session_state.player_name = None
            st.session_state.player_icon = None
            st.rerun()
        return

    if st.button("Leave Game"):
        st.session_state.game_code = None
        st.session_state.is_host = False
        st.session_state.player_name = None
        st.session_state.player_icon = None
        st.rerun()

    st.markdown("---")

    if game["status"] == "lobby":
        show_lobby_view(game_code, is_host, game)
    else:
        show_draft_view(game_code, is_host, game)


# ---------- Main app routing ----------

if st.session_state.game_code is None:
    show_landing_page()
else:
    show_game_page(st.session_state.game_code, st.session_state.is_host)

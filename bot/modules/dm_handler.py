from classes import Player, PlayerStat, MatchlogStat, StreamlitApp, MyFancyClass
from match.classes import Match
import modules.config as cfg
from display import AllStrings as disp, ContextWrapper
from logging import getLogger
import modules.stat_processor as stat_processor
import modules.spam_checker as spam_checker
import modules.database as db

from interactive_stats import dump_pkl

log = getLogger("pog_bot")


async def on_dm(message):
    # Check if too many requests from this user:
    if await spam_checker.is_spam(message.author, message.channel):
        return
    if message.content[:1] == "=":
        message.content = message.content[1:]
    if message.content.lower().startswith(("stat", "stats", "s")):
        await on_stats(message.author)
    elif message.content.lower().startswith(("modmail ", "dm ", "staff ")):
        i = message.content.index(' ')
        message.content = message.content[i+1:]
        player = Player.get(message.author.id)
        await disp.BOT_DM.send(ContextWrapper.channel(cfg.channels["staff"]), player=player, msg=message)
        await disp.BOT_DM_RECEIVED.send(message.author)
    elif message.content.lower().startswith(("help", "h")):
        await disp.HELP.send(message.author, is_dm=True)
    spam_checker.unlock(message.author.id)


async def on_stats(user):
    player = Player.get(user.id)
    if not player:
        await disp.NO_RULE.send(user, "stats", cfg.channels["rules"]) # if the player isn't registered, he can't see his stats. We send them to the rules channel.
        return
    log.info(f"Stats request from player id: [{player.id}], name: [{player.name}]")

    #FOR DEGUBBING ONLY:
    all_stats = await PlayerStat.get_from_database(176428787968376833, "Saiyan")
    #all_stats = await PlayerStat.get_from_database(player.id, player.name)
    recent_stats = await stat_processor.get_new_stats(Match, all_stats)
    await db.async_db_call(db.get_all_elements, MatchlogStat, "match_logs", True)  # True flag allows us to force the loop to continue even with bad match data, because some matches are cleared before finishing and as such may be missing data
    matchlog_stats = MatchlogStat.get_all()
    streamlit_instance = StreamlitApp(player.id, player.name, all_stats, matchlog_stats) # spawns a streamlit app
    await disp.DISPLAY_STATS.send(user, stats=all_stats, recent_stats=recent_stats, streamlit_url=streamlit_instance.url) # displays streamlit url in discord bot DM using the strings.py and embeds.py modules

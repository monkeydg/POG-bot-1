"""main.py

Initialize everything, attach the general handlers, run the client.
The application should be launched from this file
"""

# discord.py
from discord.ext import commands
from discord.ext.commands import Bot
from discord import Status, DMChannel

# Other modules
from asyncio import sleep
from random import seed
from datetime import datetime as dt
import logging
from time import gmtime
from logging.handlers import RotatingFileHandler

# Custom modules
import modules.config as cfg
from modules.display import send, channelSend, edit, init as displayInit
from modules.spam import isSpam, unlock
from modules.exceptions import ElementNotFound, UnexpectedError
from modules.database import init as dbInit, getAllItems
from modules.enumerations import PlayerStatus
from modules.loader import init as cogInit, isAllLocked, unlockAll
from modules.roles import init as rolesInit, roleUpdate, isAdmin
from modules.reactions import reactionHandler

# Modules for the custom classes
from matches import onInactiveConfirmed, init as matchesInit
from classes.players import Player, getPlayer, getAllPlayersList
from classes.accounts import AccountHander
from classes.maps import Map
from classes.weapons import Weapon


def _addMainHandlers(client):
    """_addMainHandlers, private function
        Parameters
        ----------
        client : discord.py bot
            Our bot object
    """

    rulesMsg = None  # Will contain message object representing the rules message, global variable

    # help command, works in all channels
    @client.command(aliases=['h'])
    @commands.guild_only()
    async def help(ctx):
        await send("HELP", ctx)

    # Slight anti-spam: prevent the user to input a command if the last one isn't yet processed
    # Useful for the long processes like ps2 api, database or spreadsheet calls
    @client.event
    async def on_message(message):
        if message.author == client.user:  # if bot, do nothing
            await client.process_commands(message)
            return
        # if dm, print in console and ignore the message
        if isinstance(message.channel, DMChannel):
            logging.info(message.author.name + ": " + message.content)
            return
        if message.channel.id not in cfg.channelsList:
            return
        if isAllLocked():
            if not isAdmin(message.author):
                return
            # Admins can still use bot when locked
        if await isSpam(message):
            return
        message.content = message.content.lower()
        await client.process_commands(message)  # if not spam, process
        await sleep(0.5)
        unlock(message.author.id)  # call finished, we can release user

    # Global command error handler
    @client.event
    async def on_command_error(ctx, error):
        if isinstance(error, commands.CommandNotFound):  # Unknown command
            if isAllLocked():
                await send("BOT_IS_LOCKED", ctx)
                return
            await send("INVALID_COMMAND", ctx)
            return
        if isinstance(error, commands.errors.CheckFailure):  # Unauthorized command
            cogName = ctx.command.cog.qualified_name
            if cogName == "admin":
                await send("NO_PERMISSION", ctx, ctx.command.name)
                return
            try:
                channelId = cfg.channels[cogName]
                channelStr = ""
                if isinstance(channelId, list):
                    channelStr = "channels " + \
                        ", ".join(f'<#{id}>' for id in channelId)
                else:
                    channelStr = f'channel <#{channelId}>'
                # Send the use back to the right channel
                await send("WRONG_CHANNEL", ctx, ctx.command.name, channelStr)
            except KeyError:  # Should not happen
                await send("UNKNOWN_ERROR", ctx, "Channel key error")
            return
        # These are annoying error generated by discord.py when user input quotes (")
        bl = isinstance(error, commands.errors.InvalidEndOfQuotedStringError)
        bl = bl or isinstance(error, commands.errors.ExpectedClosingQuoteError)
        bl = bl or isinstance(error, commands.errors.UnexpectedQuoteError)
        if bl:
            # Tell the user not to use quotes
            await send("INVALID_STR", ctx, '"')
            return
        if isinstance(error.original, UnexpectedError):
            await send("UNKNOWN_ERROR", ctx, error.original.reason)
        else:
            # Print unhandled error
            await send("UNKNOWN_ERROR", ctx, type(error.original).__name__)
        raise error

    # Reaction update handler (for rule acceptance)
    @client.event
    # Has to be on_raw cause the message already exists when the bot starts
    async def on_raw_reaction_add(payload):
        if payload.member is None or payload.member.bot:  # If bot, do nothing
            return
        if isAllLocked():
            return
        # reaction to the rule message?
        if payload.message_id == cfg.general["rules_msg_id"]:
            global rulesMsg
            if str(payload.emoji) == "✅":
                try:
                    p = getPlayer(payload.member.id)
                except ElementNotFound:  # if new player
                    # create a new profile
                    p = Player(payload.member.name, payload.member.id)
                await roleUpdate(p)
                if p.status is PlayerStatus.IS_NOT_REGISTERED:
                        # they can now register
                        await channelSend("REG_RULES", cfg.channels["register"], payload.member.mention)
            # In any case remove the reaction, message is to stay clean
            await rulesMsg.remove_reaction(payload.emoji, payload.member)

    # Reaction update handler (for accounts)
    @client.event
    async def on_reaction_add(reaction, user):
        try:
            player = getPlayer(user.id)
        except ElementNotFound:
            return
        await reactionHandler(reaction, player)

    @client.event
    async def on_member_join(member):
        try:
            player = getPlayer(member.id)
        except ElementNotFound:
            return
        await roleUpdate(player)

    @client.event
    async def on_member_update(before, after):
        if before.status != after.status:
            await on_status_update(after)

    # Status update handler (for inactivity)
    async def on_status_update(user):
        try:
            player = getPlayer(user.id)
        except ElementNotFound:
            return
        if user.status == Status.offline:
            player.onInactive(onInactiveConfirmed)
        else:
            player.onActive()
        await roleUpdate(player)


def _addInitHandlers(client):

    @client.event
    async def on_ready():
        rolesInit(client)

        # fetch rule message, remove all reaction but the bot's
        global rulesMsg
        rulesMsg = await client.get_channel(cfg.channels["rules"]).fetch_message(cfg.general["rules_msg_id"])
        await rulesMsg.clear_reactions()
        await sleep(0.2)
        await rulesMsg.add_reaction('✅')

        # Update all players roles
        for p in getAllPlayersList():
            await roleUpdate(p)
        _addMainHandlers(client)
        unlockAll(client)
        logging.info('Client is ready!')

    @client.event
    async def on_message(message):
        return


# TODO: testing, to be removed
def _test(client):
    from test2 import testHand
    testHand(client)


def main(launchStr=""):

    # Logging config
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logging.Formatter.converter = gmtime
    formatter = logging.Formatter('%(asctime)s | %(levelname)s %(message)s', "%Y-%m-%d %H:%M:%S UTC")
    file_handler = RotatingFileHandler('../logging/bot_log.out', 'a', 1000000, 1)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Init order MATTERS

    # Seeding random generator
    seed(dt.now())

    # Get data from the config file
    cfg.getConfig(f"config{launchStr}.cfg")

    # Set up command prefix
    client = commands.Bot(command_prefix=cfg.general["command_prefix"])

    # Remove default help
    client.remove_command('help')

    # Initialise db and get all t=xhe registered users and all maps from it
    dbInit(cfg.database)
    getAllItems(Player.newFromData, "users")
    getAllItems(Map, "sBases")
    getAllItems(Weapon, "sWeapons")

    # Get Account sheet from drive
    AccountHander.init(f"client_secret{launchStr}.json")

    # Initialise matches channels
    matchesInit(cfg.channels["matches"])

    # Initialise display module
    displayInit(client)

    # Add main handlers
    _addInitHandlers(client)
    if launchStr == "_test":
        _test(client)

    # Add all cogs
    cogInit(client)



    # Run server
    client.run(cfg.general["token"])


if __name__ == "__main__":
    # execute only if run as a script
    # Use main() for production

    # main("_test")
    main()

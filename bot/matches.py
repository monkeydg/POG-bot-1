import modules.config as cfg
from modules.exceptions import UnexpectedError, AccountsNotEnough, ElementNotFound
from modules.display import channelSend, edit
from modules.enumerations import PlayerStatus, MatchStatus, SelStatus
from modules.imageMaker import publishMatchImage
from modules.script import processScore
from datetime import datetime as dt

from classes.teams import Team  # ok
from classes.players import TeamCaptain, ActivePlayer  # ok
from classes.maps import MapSelection, mainMapPool  # ok
from classes.accounts import AccountHander  # ok

from random import choice as randomChoice
from lib import tasks
from asyncio import sleep
from logging import getLogger

log = getLogger(__name__)

_lobbyList = list()
_lobbyStuck = False
_allMatches = dict()


def getMatch(id):
    if id not in _allMatches:
        raise ElementNotFound(id)  # should never happen
    return _allMatches[id]


def isLobbyStuck():
    global _lobbyStuck
    return _lobbyStuck


def _autoPingTreshold():
    tresh = cfg.general["lobby_size"] - cfg.general["lobby_size"] // 3
    return tresh


def _autoPingCancel():
    _autoPing.cancel()
    _autoPing.already = False


def addToLobby(player):
    _lobbyList.append(player)
    player.onLobbyAdd()
    if len(_lobbyList) == cfg.general["lobby_size"]:
        startMatchFromFullLobby.start()
    elif len(_lobbyList) >= _autoPingTreshold():
        if not _autoPing.is_running() and not _autoPing.already:
            _autoPing.start()
            _autoPing.already = True


@tasks.loop(minutes=3, delay=1, count=2)
async def _autoPing():
    if _findSpotForMatch() is None:
        return
    await channelSend("LB_NOTIFY", cfg.channels["lobby"], f'<@&{cfg.roles["notify"]}>')
_autoPing.already = False


def getLobbyLen():
    return len(_lobbyList)


def getAllNamesInLobby():
    names = [p.mention for p in _lobbyList]
    return names


def removeFromLobby(player):
    _lobbyList.remove(player)
    _onLobbyRemove()
    player.onLobbyLeave()


def _onMatchFree():
    _autoPing.already = True
    if len(_lobbyList) == cfg.general["lobby_size"]:
        startMatchFromFullLobby.start()


def _onLobbyRemove():
    global _lobbyStuck
    _lobbyStuck = False
    if len(_lobbyList) < _autoPingTreshold():
        _autoPingCancel()


@tasks.loop(count=1)
async def startMatchFromFullLobby():
    global _lobbyStuck
    match = _findSpotForMatch()
    _autoPingCancel()
    if match is None:
        _lobbyStuck = True
        await channelSend("LB_STUCK", cfg.channels["lobby"])
        return
    _lobbyStuck = False
    match._setPlayerList(_lobbyList)
    for p in _lobbyList:
        # Player status is modified automatically in IS_MATCHED
        p.onMatchSelected(match)
    _lobbyList.clear()
    match._launch.start()
    await channelSend("LB_MATCH_STARTING", cfg.channels["lobby"], match.id)


async def onInactiveConfirmed(player):
    removeFromLobby(player)
    await channelSend("LB_WENT_INACTIVE", cfg.channels["lobby"], player.mention, namesInLobby=getAllNamesInLobby())


def clearLobby():
    if len(_lobbyList) == 0:
        return False
    for p in _lobbyList:
        p.onLobbyLeave()
    _lobbyList.clear()
    _onLobbyRemove()
    return True


def _findSpotForMatch():
    for match in _allMatches.values():
        if match.status is MatchStatus.IS_FREE:
            return match
    return None


def init(list):
    for id in list:
        Match(id)


class Match():

    def __init__(self, id):
        self.__id = id
        self.__players = dict()
        self.__status = MatchStatus.IS_FREE
        self.__teams = [None, None]
        self.__mapSelector = None
        self.__number = 0
        _allMatches[id] = self
        self.__accounts = None
        self.__roundsStamps = list()

    @property
    def status(self):
        return self.__status

    @property
    def id(self):
        return self.__id

    @property
    def teams(self):
        return self.__teams

    @property
    def statusString(self):
        return self.__status.value

    @property
    def number(self):
        return self.__number

    @number.setter
    def number(self, num):
        self.__number = num

    @property
    def playerPings(self):
        pings = [p.mention for p in self.__players.values()]
        return pings

    def _setPlayerList(self, pList):
        self.__status = MatchStatus.IS_RUNNING
        for p in pList:
            self.__players[p.id] = p

    def pick(self, team, player):
        team.addPlayer(ActivePlayer, player)
        self.__players.pop(player.id)
        team.captain.isTurn = False
        other = self.__teams[team.id-1]
        other.captain.isTurn = True
        if len(self.__players) == 1:
            # Auto pick
            p = [*self.__players.values()][0]
            self.__pingLastPlayer.start(other, p)
            return self.pick(other, p)
        if len(self.__players) == 0:
            self.__status = MatchStatus.IS_FACTION
            self.__teams[1].captain.isTurn = True
            self.__teams[0].captain.isTurn = False
            picker = self.__teams[1].captain
            self.__playerPickOver.start(picker)
            return self.__teams[1].captain
        return other.captain

    def confirmMap(self):
        self.__mapSelector.confirm()
        if self.__status is MatchStatus.IS_MAPPING:
            self.__ready.start()

    def pickMap(self, captain):
        if self.__mapSelector.status is SelStatus.IS_SELECTED:
            captain.isTurn = False
            other = self.__teams[captain.team.id-1]
            other.captain.isTurn = True
            return other.captain
        return captain

    def resign(self, captain):
        team = captain.team
        if team.isPlayers:
            return False
        else:
            player = captain.onResign()
            key = randomChoice(list(self.__players))
            self.__players[player.id] = player
            team.clear()
            team.addPlayer(TeamCaptain, self.__players.pop(key))
            team.captain.isTurn = captain.isTurn
            return True

    @tasks.loop(count=1)
    async def __pingLastPlayer(self, team, p):
        await channelSend("PK_LAST", self.__id, p.mention, team.name)

    @tasks.loop(count=1)
    async def __playerPickOver(self, picker):
        await channelSend("PK_OK_FACTION", self.__id, picker.mention, match=self)

    def factionPick(self, team, str):
        faction = cfg.i_factions[str]
        other = self.__teams[team.id-1]
        if other.faction == faction:
            return team.captain
        team.faction = faction
        team.captain.isTurn = False
        if other.faction != 0:
            self.__status = MatchStatus.IS_MAPPING
            self.__findMap.start()
        else:
            other.captain.isTurn = True
        return other.captain

    def onTeamReady(self, team):
        notReady = list()
        for p in team.players:
            if p.hasOwnAccount:
                continue
            if p.account is None:
                log.error(f"Debug: {p.name} has no account")
            if p.account.isValidated:
                continue
            notReady.append(p.mention)
        if len(notReady) != 0:
            return notReady
        team.captain.isTurn = False
        other = self.__teams[team.id-1]
        # If other isTurn, then not ready
        # Else everyone ready
        if not other.captain.isTurn:
            self.__status = MatchStatus.IS_STARTING
            self.__startMatch.start()

    @tasks.loop(count=1)
    async def __findMap(self):
        for tm in self.__teams:
            tm.captain.isTurn = True
        if self.__mapSelector.status is SelStatus.IS_CONFIRMED:
            await channelSend("MATCH_MAP_AUTO", self.__id, self.__mapSelector.map.name)
            self.__ready.start()
            return
        captainPings = [tm.captain.mention for tm in self.__teams]
        self.__status = MatchStatus.IS_MAPPING
        await channelSend("PK_WAIT_MAP", self.__id, *captainPings)
        # if self.__mapSelector.isSmallPool:
        #     await channelSend("MAP_SHOW_POOL", self.__id, sel=self.__mapSelector)

    @tasks.loop(count=1)
    async def __ready(self):
        self.__status = MatchStatus.IS_RUNNING
        for tm in self.__teams:
            tm.onMatchReady()
            tm.captain.isTurn = True
        captainPings = [tm.captain.mention for tm in self.__teams]
        try:
            await self.__accounts.give_accounts()
        except AccountsNotEnough:
            await channelSend("ACC_NOT_ENOUGH", self.__id)
            await self.clear()
            return
        self.__status = MatchStatus.IS_WAITING
        await channelSend("MATCH_CONFIRM", self.__id, *captainPings, match=self)

    @tasks.loop(minutes=cfg.ROUND_LENGHT, delay=1, count=2)
    async def _onMatchOver(self):
        playerPings = [" ".join(tm.allPings) for tm in self.__teams]
        await channelSend("MATCH_ROUND_OVER", self.__id, *playerPings, self.roundNo)
        self._scoreCalculation.start()
        for tm in self.__teams:
            tm.captain.isTurn = True
        if self.roundNo < 2:
            await channelSend("MATCH_SWAP", self.__id)
            self.__status = MatchStatus.IS_WAITING
            captainPings = [tm.captain.mention for tm in self.__teams]
            await channelSend("MATCH_CONFIRM", self.__id, *captainPings, match=self)
            return
        await channelSend("MATCH_OVER", self.__id)
        self.__status = MatchStatus.IS_RUNNING
        await self.clear()

    @tasks.loop(count=1)
    async def _scoreCalculation(self):
        await processScore(self)
        await publishMatchImage(self)


    @tasks.loop(count=1)
    async def __startMatch(self):
        await channelSend("MATCH_STARTING_1", self.__id, self.roundNo, "30")
        await sleep(10)
        await channelSend("MATCH_STARTING_2", self.__id, self.roundNo, "20")
        await sleep(10)
        await channelSend("MATCH_STARTING_2", self.__id, self.roundNo, "10")
        await sleep(10)
        playerPings = [" ".join(tm.allPings) for tm in self.__teams]
        await channelSend("MATCH_STARTED", self.__id, *playerPings, self.roundNo)
        self.__roundsStamps.append(int(dt.timestamp(dt.now())))
        self.__status = MatchStatus.IS_PLAYING
        self._onMatchOver.start()

    @tasks.loop(count=1)
    async def _launch(self):
        await channelSend("MATCH_INIT", self.__id, " ".join(self.playerPings))
        self.__accounts = AccountHander(self)
        self.__mapSelector = MapSelection(self, mainMapPool)
        for i in range(len(self.__teams)):
            self.__teams[i] = Team(i, f"Team {i+1}", self)
            key = randomChoice(list(self.__players))
            self.__teams[i].addPlayer(TeamCaptain, self.__players.pop(key))
        self.__teams[0].captain.isTurn = True
        self.__status = MatchStatus.IS_PICKING
        await channelSend("MATCH_SHOW_PICKS", self.__id, self.__teams[0].captain.mention, match=self)

    async def clear(self):
        """ Clearing match and base player objetcts
        Team and ActivePlayer objects should get garbage collected, nothing is referencing them anymore"""

        if self.status is MatchStatus.IS_PLAYING:
            self._onMatchOver.cancel()
            playerPings = [" ".join(tm.allPings) for tm in self.__teams]
            await channelSend("MATCH_ROUND_OVER", self.__id, *playerPings, self.roundNo)
            await channelSend("MATCH_OVER", self.__id)

        # Updating account sheet with current match
        await self.__accounts.doUpdate()

        # Clean players if left in the list
        for p in self.__players.values():
            p.onPlayerClean()

        # Clean players if in teams
        for tm in self.__teams:
            for aPlayer in tm.players:
                aPlayer.clean()

        # Clean mapSelector
        self.__mapSelector.clean()

        # Release all objects:
        self.__accounts = None
        self.__mapSelector = None
        self.__teams = [None, None]
        self.__roundsStamps.clear()
        self.__players.clear()
        await channelSend("MATCH_CLEARED", self.__id)
        self.__status = MatchStatus.IS_FREE
        _onMatchFree()

    @property
    def map(self):
        if self.__mapSelector.status is SelStatus.IS_CONFIRMED:
            return self.__mapSelector.map

    # TODO: testing only
    @property
    def players(self):
        return self.__players

    @property
    def roundNo(self):
        if self.__status is MatchStatus.IS_PLAYING:
            return len(self.__roundsStamps)
        if self.__status in (MatchStatus.IS_STARTING, MatchStatus.IS_WAITING):
            return len(self.__roundsStamps)+1
        return 0

    @property
    def startStamp(self):
        return self.__roundsStamps[-1]

    @property
    def mapSelector(self):
        return self.__mapSelector

    # TODO: DEV
    @teams.setter
    def teams(self, tms):
        self.__teams=tms
    
    # TODO: DEV
    @startStamp.setter
    def startStamp(self, st):
        self.__roundsStamps = st
    
    # TODO: DEV
    @mapSelector.setter
    def mapSelector(self, ms):
        self.__mapSelector = ms

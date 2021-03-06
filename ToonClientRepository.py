from panda3d.core import *
from direct.distributed.ClientRepositoryBase import ClientRepositoryBase
from direct.distributed.MsgTypes import *
from direct.distributed.PyDatagram import PyDatagram
from direct.distributed.PyDatagramIterator import PyDatagramIterator
from direct.distributed import DistributedSmoothNode
from direct.gui.DirectGui import *
from direct.showbase.DirectObject import DirectObject
from direct.interval.IntervalGlobal import *
import sys
import random
import math
import os
import Globals, LocalToon

# Using ClientRepositoryBase since AstronClientRepository
# is not finished and possibly not usable yet.
class ToonClientRepository(ClientRepositoryBase):
    taskChain = 'net'

    def __init__(self, playerName = None, threadedNet = True):
        dcFileNames = ['direct.dc', 'toon.dc']
        
        ClientRepositoryBase.__init__(self, dcFileNames = dcFileNames,
                                  connectMethod = self.CM_NET,
                                  threadedNet = threadedNet)

        self.GameGlobalsId = 1000

        self.zoneInterest = None

        self.visInterest = None

        self.avatarManager = self.generateGlobalObject(1001, 'AvatarManager')

        base.transitions.FadeModelName = 'resources/phase_3/models/misc/fade.bam'

        self.gameVersion = base.config.GetString('server-version', 'dev')

        self.toonMgr = None

        # Allow some time for other processes.  This also allows time
        # each frame for the network thread to run.
        base.setSleep(0.01)

        base.disableMouse()

        # No game, no avatar (yet).
        base.localAvatar = None

    def lostConnection(self):
        # This should be overridden by a derived class to handle an
        # unexpectedly lost connection to the gameserver.
        self.notify.warning("Lost connection to gameserver.")

        cbMgr = CullBinManager.getGlobalPtr()
        cbMgr.addBin('gui-popup', cbMgr.BTUnsorted, 60)

        self.failureText = OnscreenText(
            'Lost connection to gameserver.\nPress ESC to quit.',
            scale = 0.15, fg = (1, 0, 0, 1), shadow = (0, 0, 0, 1),
            pos = (0, 0.2))
        self.failureText.setBin('gui-popup', 0)
        base.transitions.fadeScreen(alpha = 1)
        render.hide()

        self.ignore('escape')
        self.accept('escape', self.exit)
        self.accept('control-escape', self.exit)

    def exit(self):
        if self.isConnected():
            self.sendDisconnect()
            self.disconnect()
            
        sys.exit()

    def startConnect(self):
        self.url = None
        if not self.url:
            tcpPort = base.config.GetInt('server-port', 7198)
            hostname = base.config.GetString('server-host', 'localhost')
            if not hostname:
                hostname = 'localhost'
            self.url = URLSpec('g://%s:%s' % (hostname, tcpPort))
        
        self.waitingText = OnscreenText(
            'Connecting to %s.\nPress ESC to cancel.' % (self.url),
            scale = 0.1, fg = (1, 1, 1, 1), shadow = (0, 0, 0, 1))

        self.connect([self.url],
                     successCallback = self.connectSuccess,
                     failureCallback = self.connectFailure)

    def escape(self):
        """ The user pressed escape.  Exit the client. """
        self.exit()
        
    def connectFailure(self, statusCode, statusString):
        self.waitingText.destroy()
        self.failureText = OnscreenText(
            'Failed to connect to %s:\n%s.\nPress ESC to quit.' % (self.url, statusString),
            scale = 0.15, fg = (1, 0, 0, 1), shadow = (0, 0, 0, 1),
            pos = (0, 0.2))

    def makeWaitingText(self):
        if self.waitingText:
            self.waitingText.destroy()
        self.waitingText = OnscreenText(
            'Waiting for server.',
            scale = 0.1, fg = (1, 1, 1, 1), shadow = (0, 0, 0, 1))

    def connectSuccess(self):
        """ Successfully connected.  But we still can't really do
        anything until we send an CLIENT_HELLO message. """
        self.makeWaitingText()

        dg = PyDatagram()
        dg.addUint16(CLIENT_HELLO)
        dg.addUint32(self.hashVal)
        dg.addString(self.gameVersion)
        self.send(dg)

        # Make sure we have interest in the TimeManager zone, so we
        # always see it even if we switch to another zone.
        #self.setInterestZones([1])

        # We must wait for the TimeManager to be fully created and
        # synced before we can enter zone 2 and wait for the game
        # object.
        #self.acceptOnce(self.uniqueName('gotTimeSync'), self.syncReady)

    def handleDatagram(self, di):
        msgType = self.getMsgType()
        if msgType == CLIENT_HELLO_RESP:
            self.handleHelloResp()
        elif msgType == CLIENT_OBJECT_SET_FIELD:
            self.handleUpdateField(di)
        elif msgType == CLIENT_ENTER_OBJECT_REQUIRED:
            self.handleGenerate(di)
        elif msgType == CLIENT_ENTER_OBJECT_REQUIRED_OTHER:
            self.handleGenerate(di, True)
        elif msgType == CLIENT_DONE_INTEREST_RESP:
            self.handleInterestDoneMessage(di)
        elif msgType == CLIENT_ENTER_OBJECT_REQUIRED_OWNER:
            self.handleGenerateOwner(di)
        elif msgType == CLIENT_ENTER_OBJECT_REQUIRED_OTHER_OWNER:
            self.handleGenerateOwner(di, True)
        elif msgType == CLIENT_OBJECT_LEAVING:
            self.handleDelete(di)

    def handleHelloResp(self):
        # self.startHeartbeat()
        self.acceptOnce('accessResponse', self.handleResponse)
        self.avatarManager.requestAccess()

    def sendHeartbeat(self):
        dg = PyDatagram()
        dg.addUint16(CLIENT_HEARTBEAT)
        self.send(dg)

    def sendDisconnect(self):
        print 'Sending disconnect messsage'
        dg = PyDatagram()
        dg.addUint16(CLIENT_DISCONNECT)
        self.send(dg)

    def handleGenerate(self, di, other = False):
        doId = di.getUint32()
        parentId = di.getUint32()
        zoneId = di.getUint32()
        classId = di.getUint16()

        dclass = self.dclassesByNumber[classId]
        dclass.startGenerate()
        if other:
            self.generateWithRequiredOtherFields(dclass, doId, di, parentId, zoneId)
        else:
            self.generateWithRequiredFields(dclass, doId, di, parentId, zoneId)
        dclass.stopGenerate()
        self.notify.warning(dclass.getName())

    def handleGenerateOwner(self, di, other = False):
        # Toontown only makes use of OwnerViews for LocalToon.
        doId = di.getUint32()
        parentId = di.getUint32()
        zoneId = di.getUint32()
        dclassId = di.getUint16()
        self.handleAvatarResponseMsg(doId, di)

    def handleAvatarResponseMsg(self, avatarId, di):
        dclass = self.dclassesByName['DistributedToon']
        localAvatar = LocalToon.LocalToon(self)
        localAvatar.dclass = dclass
        base.localAvatar = localAvatar
        localAvatar.doId = avatarId
        self.localAvatarDoId = avatarId
        parentId = None
        zoneId = None
        localAvatar.setLocation(parentId, zoneId)
        localAvatar.generateInit()
        localAvatar.generate()
        dclass.receiveUpdateBroadcastRequiredOwner(localAvatar, di)
        localAvatar.announceGenerate()
        localAvatar.postGenerateMessage()
        self.doId2do[avatarId] = localAvatar
        self.localAvatarGenerated(localAvatar)

    def handleDelete(self, di):
        doId = di.getUint32()
        if doId in self.doId2do:
            obj = self.doId2do[doId]
            del self.doId2do[doId]
            obj.deleteOrDelay()
    
    def locateAvatar(self, zoneId):
        if base.localAvatar:
            dg = PyDatagram()
            dg.addUint16(CLIENT_OBJECT_LOCATION)
            dg.addUint32(base.localAvatar.doId)
            dg.addUint32(self.timeManager.doId)
            dg.addUint32(zoneId)
            self.send(dg)

    def handleResponse(self, resp):
        if resp == 1:
            self.acceptOnce(self.uniqueName('gotTimeSync'), self.syncReady)
            self.mgrInterest = self.addInterest(self.GameGlobalsId, 1, 'game manager')

    def syncReady(self):
        """ Now we've got the TimeManager manifested, and we're in
        sync with the server time.  Now we can enter the world.  Check
        to see if we've received our doIdBase yet. """
        if self.toonMgr:
            self.toonMgr.d_requestAvatar()
        else:
            self.acceptOnce(self.uniqueName('gotToonMgr'), self.getAv)
        DistributedSmoothNode.globalActivateSmoothing(1, 0)

        #if self.haveCreateAuthority():
        #    self.gotCreateReady()
        #else:
            # Not yet, keep waiting a bit longer.
        #    self.accept(self.uniqueName('createReady'), self.gotCreateReady)

    def getAv(self):
        self.notify.warning('getAv')
        self.toonMgr.d_requestAvatar()

    def localAvatarGenerated(self, av):
        self.waitingText.destroy()
        base.camera.reparentTo(base.localAvatar.toonActor)
        base.camera.setPos(0, -15.0 - base.localAvatar.height, 1 + base.localAvatar.height)
        dropPoint = random.choice(Globals.hoodDropPoints[2000])
        base.localAvatar.toonActor.setPosHpr(dropPoint[0], dropPoint[1], dropPoint[2], dropPoint[3], dropPoint[4], dropPoint[5]) # Todo -- handle this depending on hood
        base.localAvatar.setZoneInformation(2000, 2000)
        print base.localAvatar.zoneId

    def changeAvZone(self, zoneId):
        """ Move the avatar into the indicated zone. """
        self.setObjectZone(base.localAvatar, zoneId)        

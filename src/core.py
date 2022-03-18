import logging
import re
from datetime import datetime, timedelta
from threading import Lock

import src.replies as rp
from src.globals import *
from src.database import User, SystemConfig
from src.cache import CachedMessage
from src.util import genTripcode

db = None # SQLiteDatabase
ch = None # Cache
spam_scores = None
tripcode_last_used = {} # uid -> datetime

whitelist = None
lockdown = None
blacklist_contact = None
enable_expose = None
allow_remove_command = None
media_limit_period = None
tripcode_interval = None
tripcode_toggle = None

def init(config, _db, _ch):
	global db, ch, spam_scores, whitelist, lockdown, blacklist_contact, enable_expose, allow_remove_command, media_limit_period, tripcode_interval, tripcode_toggle
	db = _db
	ch = _ch
	spam_scores = ScoreKeeper()

	whitelist = config.get("whitelist",False)
	lockdown = False
	blacklist_contact = config.get("blacklist_contact", "")
	enable_expose = config.get("enable_expose",False)
	allow_remove_command = config.get("allow_remove_command",False)
	if "media_limit_period" in config.keys():
		media_limit_period = timedelta(hours=int(config["media_limit_period"]))
	tripcode_interval = timedelta(hours=float(config.get("tripcode_limit_interval", 0)))
	tripcode_toggle = config.get("tripcode_toggle",False)

	if config.get("locale"):
		rp.localization = __import__("src.replies_" + config["locale"],
			fromlist=["localization"]).localization

	# initialize db if empty
	if db.getSystemConfig() is None:
		c = SystemConfig()
		c.defaults()
		db.setSystemConfig(c)

	# update users for new Tripcode DB
	for user in db.iterateUsers():
		if not user.tripcode or user.tripname:
			continue
		tripname, triphash = genTripcode(user.tripcode, user.salt)
		with db.modifyUser(id=user.id) as user:
			user.tripname = tripname
			user.triphash = triphash
			

def register_tasks(sched):
	# spam score handling
	sched.register(spam_scores.scheduledTask, seconds=SPAM_INTERVAL_SECONDS)
	# warning removal
	def task():
		now = datetime.now()
		for user in db.iterateUsers():
			if not user.isJoined():
				continue
			if user.warnExpiry is not None and now >= user.warnExpiry:
				with db.modifyUser(id=user.id) as user:
					user.removeWarning()
	sched.register(task, minutes=15)

def updateUserFromEvent(user, c_user):
	user.username = c_user.username
	user.realname = c_user.realname
	user.lastActive = datetime.now()

def getUserByName(username):
	if not username:
		return None
	username = str(username).strip()
	if username.startswith("##"):
		username = username[2:]

	if len(username) < 5:
		r_user = None
		user_count = 0
		for user in db.iterateUsers():
			if len(username) < 5 and user.getObfuscatedId() == username:
				r_user = user	
				user_count+=1
		if user_count <= 1:
			return r_user
		return -1
	elif username.find("!")>0:
		r_user = None
		user_count = 0
		for user in db.iterateUsers():
			if user.tripname is not None and user.tripname+user.triphash == username:
				r_user = user
		if user_count <= 1:
			return r_user
		return -1
	elif username.startswith("@"):
		username = username.lower()
		for user in db.iterateUsers():
			if user.username is not None and "@"+user.username.lower() == username:
				return user
	elif re.search("^[0-9+]{5,}$",username) is not None:
		try:
			return db.getUser(id=username)
		except KeyError as e:
			return None
	return None

def isTooSensitive(arg, user):
	arg = str(arg)
	# user IDs are not 'sensitive' unless prepended with "##". This allows IDs to be passed in by other parts of the program. Any ID typed by the moderators should be prepended before coming here.
	# is username and not tripcode, or is ID, and user not admin?
	return (arg.startswith("##@") and arg.find("!") < 0 or re.search("^##[0-9+]{5,}$",arg) is not None) and user.rank <= RANKS.admin
	#FIX: should I be taking an @ out of tripcodes?

def requireUser(func):
	def wrapper(c_user, *args, **kwargs):
		if isinstance(c_user, User):
			user = c_user
		else:
			# fetch user from db
			try:
				user = db.getUser(id=c_user.id)
			except KeyError as e:
				return rp.Reply(rp.types.USER_NOT_IN_CHAT)

		# keep db entry up to date
		with db.modifyUser(id=user.id) as user:
			updateUserFromEvent(user, c_user)

		# check for blacklist or absence
		if user.isBlacklisted():
			return rp.Reply(rp.types.ERR_BLACKLISTED, reason=user.blacklistReason, contact=blacklist_contact)
		elif not user.isJoined():
			return rp.Reply(rp.types.USER_NOT_IN_CHAT)

		# call original function
		return func(user, *args, **kwargs)
	return wrapper

def requireRank(need_rank):
	def f(func):
		def wrapper(user, *args, **kwargs):
			if not isinstance(user, User):
				raise SyntaxError("you fucked up the decorator order")
			if user.rank < need_rank:
				return
			return func(user, *args, **kwargs)
		return wrapper
	return f

###

# RAM cache for spam scores

class ScoreKeeper():
	def __init__(self):
		self.lock = Lock()
		self.scores = {}
	def increaseSpamScore(self, uid, n):
		with self.lock:
			s = self.scores.get(uid, 0)
			if s > SPAM_LIMIT:
				return False
			elif s + n > SPAM_LIMIT:
				self.scores[uid] = SPAM_LIMIT_HIT
				return s + n <= SPAM_LIMIT_HIT
			self.scores[uid] = s + n
			return True
	def scheduledTask(self):
		with self.lock:
			for uid in list(self.scores.keys()):
				s = self.scores[uid] - 1
				if s <= 0:
					del self.scores[uid]
				else:
					self.scores[uid] = s

###

# Event receiver template and Sender class that fwds to all registered event receivers

class Receiver():
	@staticmethod
	def reply(m, msid, who, except_who, reply_to):
		raise NotImplementedError()
	@staticmethod
	def delete(msid):
		raise NotImplementedError()
	@staticmethod
	def stop_invoked(who, delete_out):
		raise NotImplementedError()

class Sender(Receiver): # flawless class hierachy I know...
	receivers = []
	@staticmethod
	def reply(m, msid, who, except_who, reply_to):
		logging.debug("reply(m.type=%s, msid=%r, reply_to=%r)", rp.types.reverse[m.type], msid, reply_to)
		for r in Sender.receivers:
			r.reply(m, msid, who, except_who, reply_to)
	@staticmethod
	def delete(msid, user_id=None):
		logging.debug("delete(msid=%d)", msid)
		for r in Sender.receivers:
			r.delete(msid, user_id)
	@staticmethod
	def stop_invoked(who, delete_out=False):
		logging.debug("stop_invoked(who=%s)", who)
		for r in Sender.receivers:
			r.stop_invoked(who, delete_out)

def registerReceiver(obj):
	assert issubclass(obj, Receiver)
	Sender.receivers.append(obj)
	return obj

####

def user_join(c_user):
	try:
		user = db.getUser(id=c_user.id)
	except KeyError as e:
		user = None

	if user is not None:
		err = None
		if whitelist or lockdown:
			try:
				db.getWhitelistedUser(id=c_user.id)
				allowed = True
			except KeyError as e:
				allowed = False
		else:
			allowed = True

		if user.isBlacklisted():
			logging.info("%s tried to join, but was blacklisted", user)
			err = rp.Reply(rp.types.ERR_BLACKLISTED, reason=user.blacklistReason, contact=blacklist_contact)
		elif not allowed:
			logging.info("%s tried to join", user)
			if user.join_attempts < 2:
				for admin in db.iterateAdmins():
					if not admin.left:
						_push_system_message(rp.Reply(rp.types.CUSTOM,text="<i>User "+user.getObfuscatedId()+" wants to join.</i>"), who=admin)
			with db.modifyUser(id=user.id) as user:
				user.join_attempts += 1
			err = rp.Reply(rp.types.ERR_NOTWHITELISTED,  contact=blacklist_contact)
		if err is not None:
			with db.modifyUser(id=user.id) as user:
				updateUserFromEvent(user, c_user)
			return err

		# user rejoins
		with db.modifyUser(id=user.id) as user:
			updateUserFromEvent(user, c_user)
			if user.isJoined():
				return rp.Reply(rp.types.USER_IN_CHAT)
			user.setLeft(False)
			user.join_attempts = 0
		logging.info("%s rejoined chat", user)

		ret = [rp.Reply(rp.types.CHAT_JOIN)]
		motd = db.getSystemConfig().motd
		if motd != "":
			ret.append(rp.Reply(rp.types.CUSTOM, text=motd))

		return ret

	# create new user
	user = User()
	user.defaults()
	user.id = c_user.id
	updateUserFromEvent(user, c_user)
	if not any(db.iterateUserIds()):
		user.rank = RANKS.owner
		db.addWhitelistedUser(user.id)
		#FIX: make superadmin entry in system that points to this user.
		# Then we can say if db.getSystemConfig()['superadmin'] = user.id

	ret = [rp.Reply(rp.types.CHAT_JOIN)]	

	for admin in db.iterateAdmins():
		if not admin.left:
			_push_system_message(rp.Reply(rp.types.NEW_USER), who=admin) 

	try:
		db.getBlacklistedUser(id=user.id)
		logging.info("%s joined but had been blacklisted", user)
		user.setBlacklisted("")
		db.addUser(user)
		return rp.Reply(rp.types.ERR_BLACKLISTED, reason="", contact=blacklist_contact)
	except KeyError as e:
		pass

	if whitelist or lockdown:
		try:
			db.getWhitelistedUser(id=user.id)
		except KeyError as e:
			user.setLeft()
			user.join_attempts = 1
			logging.info("%s wants to be whitelisted", user)
			for admin in db.iterateAdmins():
				if not admin.left:
					_push_system_message(rp.Reply(rp.types.CUSTOM,text="<i>User "+user.getObfuscatedId()+" wants to join.</i>"), who=admin)
			db.addUser(user)
			return rp.Reply(rp.types.ERR_NOTWHITELISTED,  contact=blacklist_contact)

	logging.info("%s joined chat", user)

	motd = db.getSystemConfig().motd
	if motd != "":
		ret.append(rp.Reply(rp.types.CUSTOM, text=motd))

	db.addUser(user)
	return ret

def force_user_leave(user_id, blocked=True):
	with db.modifyUser(id=user_id) as user:
		user.setLeft()
	if blocked:
		logging.warning("Force leaving %s because bot is blocked", user)
	Sender.stop_invoked(user)

@requireUser
def user_leave(user):
	force_user_leave(user.id, blocked=False)
	logging.info("%s left chat", user)

	return rp.Reply(rp.types.CHAT_LEAVE)

@requireUser
@requireRank(RANKS.mod)
def modhelp(c_user):
	return rp.Reply(rp.types.HELP_MODERATOR)

@requireUser
@requireRank(RANKS.admin)
def adminhelp(c_user):
	return rp.Reply(rp.types.HELP_ADMIN)

@requireUser
def get_info(user):
	params = {
		"id": user.getObfuscatedId(),
		"username": (user.tripname or "anonymous") + (user.triphash or ""),
		"rank_i": user.rank,
		"rank": RANKS.reverse[user.rank],
		"karma": user.karma,
		"warnings": user.warnings,
		"warnExpiry": user.warnExpiry,
		"cooldown": user.cooldownUntil if user.isInCooldown() else None,
	}
	return rp.Reply(rp.types.USER_INFO, **params)

@requireUser
@requireRank(RANKS.mod)
def get_info_mod(user, username):
	if isTooSensitive(username, user):
		return rp.Reply(rp.types.ERR_ADMIN_SEARCH)

	user2 = getUserByName(username)
	if user2 == -1:
		return rp.Reply(rp.types.ERR_COLLISION)
	elif user2 is None:
		return rp.Reply(rp.types.ERR_NO_USER)

	params = {
		"id": user2.getObfuscatedId(),
		"username":  (user2.tripname or "anonymous") + (user2.triphash or ""),
		"rank_i": user2.rank,
		"rank": RANKS.reverse[user2.rank],
		"karma": str(user2.karma),
		"cooldown": user2.cooldownUntil if user2.isInCooldown() else None,
		"muzzled": user2.muzzled,
	}

	if params["rank_i"] > RANKS.admin:
		params["rank_i"] = RANKS.admin
		params["rank"] = RANKS.reverse[RANKS.admin]

	if user.rank <= RANKS.mod:
		# params["username"] = "anonymous"
		params["rank_i"] = RANKS.user
		params["rank"] = "unknown"

	return rp.Reply(rp.types.USER_INFO_MOD, **params)

@requireUser
def get_users(user):
	if user.rank < RANKS.mod:
		n = sum(1 for user in db.iterateUsers() if user.isJoined())
		return rp.Reply(rp.types.USERS_INFO, count=n)
	active, inactive, black = 0, 0, 0
	for user in db.iterateUsers():
		if user.isBlacklisted():
			black += 1
		elif not user.isJoined():
			inactive += 1
		else:
			active += 1
	return rp.Reply(rp.types.USERS_INFO_EXTENDED,
		active=active, inactive=inactive, blacklisted=black,
		total=active + inactive + black)

@requireUser
def get_help(user):
	help = db.getSystemConfig().help
	if help == "": return
	return rp.Reply(rp.types.CUSTOM, text=help)

@requireUser
@requireRank(RANKS.admin)
def set_help(user, arg):
	with db.modifySystemConfig() as config:
		config.help = arg
	logging.info("%s set help list.", user)
	return rp.Reply(rp.types.SUCCESS)

@requireUser
def get_motd(user):
	motd = db.getSystemConfig().motd
	if motd == "": return
	return rp.Reply(rp.types.CUSTOM, text=motd)

@requireUser
@requireRank(RANKS.admin)
def set_motd(user, arg):
	with db.modifySystemConfig() as config:
		config.motd = arg
	logging.info("%s set motd.", user)
	return rp.Reply(rp.types.SUCCESS)

@requireUser
def toggle_debug(user):
	with db.modifyUser(id=user.id) as user:
		user.debugEnabled = not user.debugEnabled
		new = user.debugEnabled
	return rp.Reply(rp.types.BOOLEAN_CONFIG, description="Debug mode", enabled=new)

@requireUser
def toggle_tripcode(user):
	if(tripcode_toggle):
		with db.modifyUser(id=user.id) as user:
			user.tripcodeToggle = not user.tripcodeToggle
			new = user.tripcodeToggle
		return rp.Reply(rp.types.BOOLEAN_CONFIG, description=(user.tripname or "anon")+(user.triphash or "")+" tripcode", enabled=new)

@requireUser
def toggle_karma(user):
	with db.modifyUser(id=user.id) as user:
		user.hideKarma = not user.hideKarma
		new = user.hideKarma
	return rp.Reply(rp.types.BOOLEAN_CONFIG, description="Karma notifications", enabled=not new)

@requireUser
def get_tripcode(user):
	return rp.Reply(rp.types.TRIPCODE_INFO, tripcode=user.tripcode)

@requireUser
def set_tripcode(user, text):
	if text.lower() == "no":
		with db.modifyUser(id=user.id) as user:
			user.tripcode = None
			user.tripname = None
			user.triphash = None
		return rp.Reply(rp.types.TRIPCODE_SET, tripname="None", triphash="")

	if tripcode_interval.total_seconds() > 1:
		last_used = tripcode_last_used.get(user.id, None)
		if last_used and (datetime.now() - last_used) < tripcode_interval:
			diff = str(tripcode_interval - (datetime.now() - last_used)+timedelta(minutes=1))
			diff = diff[:diff.rfind(":")]
			return rp.Reply(rp.types.ERR_SPAMMY_TRIPCODE,time_left=diff)

	if not (0 < text.find("#") < len(text) - 1):
		return rp.Reply(rp.types.ERR_INVALID_TRIP_FORMAT)
	if "\n" in text or text.find("#") > 18:
		return rp.Reply(rp.types.ERR_INVALID_TRIP_FORMAT)

	tripcode_last_used[user.id] = datetime.now()
	tripname, triphash = genTripcode(text, user.salt)
	with db.modifyUser(id=user.id) as user:
		user.tripcode = text
		user.tripname = tripname
		user.triphash = triphash
	return rp.Reply(rp.types.TRIPCODE_SET, tripname=tripname, triphash=triphash)

@requireUser
@requireRank(RANKS.mod)
def promote_user(user, username, rank):
	if user.rank <= rank:
		return rp.Reply(rp.types.CUSTOM, text="<i>You can only set ranks that are lower than your own.<i>")

	if isTooSensitive(username, user):
		return rp.Reply(rp.types.ERR_ADMIN_SEARCH)

	user2 = getUserByName(username)
	if user2 == -1:
		return rp.Reply(rp.types.ERR_COLLISION)
	elif user2 is None:
		return rp.Reply(rp.types.ERR_NO_USER)

	if user2.rank == RANKS.banned:
		return rp.Reply(rp.types.ERR_WITH_BLACKLISTED)
	if user2.rank >= rank:
		return rp.Reply(rp.types.CUSTOM, text="<i>They are already that rank or higher.</i>")
	with db.modifyUser(id=user2.id) as user2:
		user2.rank = rank
	if rank == RANKS.admin:
		_push_system_message(rp.Reply(rp.types.PROMOTED_ADMIN), who=user2)
	elif rank == RANKS.mod:
		_push_system_message(rp.Reply(rp.types.PROMOTED_MOD), who=user2)
	logging.info("%s was promoted by %s to: %s", user2, user, RANKS.reverse[rank])
	return rp.Reply(rp.types.SUCCESS)

@requireUser
@requireRank(RANKS.mod)
def send_mod_message(user, arg):
	text = arg + " ~<b>mods</b>"
	_push_system_message(rp.Reply(rp.types.CUSTOM, text=text))
	logging.info("%s sent mod message: %s", user, arg)

@requireUser
@requireRank(RANKS.admin)
def send_admin_message(user, arg):
	text = arg + " ~<b>admins</b>"
	_push_system_message(rp.Reply(rp.types.CUSTOM, text=text))
	logging.info("%s sent admin message: %s", user, arg)

@requireUser
@requireRank(RANKS.mod)
def warn_user(user, msid, delete=False, text="media"):
	cm = ch.getMessage(msid)
	if cm is None:
		return rp.Reply(rp.types.ERR_NOT_IN_CACHE)

	if text is None:
		text = "media"

	messages = []
	if cm.user_id is not None:
		with db.modifyUser(id=cm.user_id) as user2:
			if not cm.warned:
				# logging.info("Fix the karma system!")
				# d = timedelta(minutes=0) # for testing
				d = user2.addWarning()
				user2.karma -= KARMA_WARN_PENALTY
				if not user2.left:
					_push_system_message(
					rp.Reply(rp.types.GIVEN_COOLDOWN, duration=d, deleted=delete, text=text),
					who=user2, reply_to=msid)
				cm.warned = True
			else:
				if not delete: # allow deleting already warned messages
					return rp.Reply(rp.types.ERR_ALREADY_WARNED)
			logging.info("%s warned %s%s", user, user2, delete and "\nDeleted: " + text)
	if delete:
		Sender.delete(msid, user.id)
		if user2 is not None:
			messages.append(get_info_mod(user, user2.id))

	messages.append(rp.Reply(rp.types.SUCCESS))
	return messages

@requireUser
@requireRank(RANKS.mod)
def delete_message(user, msid, warn=True, text="media"):
	if not allow_remove_command and not warn:
		return rp.Reply(rp.types.ERR_COMMAND_DISABLED)

	cm = ch.getMessage(msid)

	Sender.delete(msid, user.id)

	if cm is None:
		return rp.Reply(rp.types.ERR_NOT_IN_CACHE)

	user2 = None
	
	# isinstance(cm,src.cache.CachedMessage)
	if not isinstance(cm, int) and cm.user_id is not None:
		user2 = db.getUser(id=cm.user_id)

	if text is None:
		text = "media"

	if user2 is not None and warn:
		# if warn and not user2.left:
		# 	_push_system_message(rp.Reply(rp.types.MESSAGE_DELETED), who=user2)#, reply_to=msid)
		logging.info("%s deleted a message from %s\nDeleted: %s", user, user2, text or "")

	messages=[]
	if user2 is not None:
		messages.append(get_info_mod(user, user2.id))
	messages.append(rp.Reply(rp.types.SUCCESS))
	return messages

@requireUser
@requireRank(RANKS.mod)
def uncooldown_user(user, username):
	if isTooSensitive(username, user):
		return rp.Reply(rp.types.ERR_ADMIN_SEARCH)

	user2 = getUserByName(username)
	if user2 == -1:
		return rp.Reply(rp.types.ERR_COLLISION)
	elif user2 is None:
		return rp.Reply(rp.types.ERR_NO_USER)

	if not user2.isInCooldown():
		return rp.Reply(rp.types.ERR_NOT_IN_COOLDOWN)
	with db.modifyUser(id=user2.id) as user2:
		user2.removeWarning()
		was_until = user2.cooldownUntil
		user2.cooldownUntil = None
	logging.info("%s removed cooldown from %s (was until %s)", user, user2, format_datetime(was_until))
	return rp.Reply(rp.types.SUCCESS)

@requireUser
@requireRank(RANKS.admin)
def show_whitelist(c_user):
	# if not whitelist:
	# 	return rp.Reply(rp.types.WHITELIST_NOT_ON)
	buttons = []
	for user in db.iterateUsers(order_by="joined",order_desc=True):
		try:
			db.getWhitelistedUser(id=user.id)
		except KeyError as e:
			if not user.isBlacklisted():
				# tag = "@"+user.username if user.username else user.realname
				tag = user.getAnonymizedName()
				tag += " (" + (user.joined-timedelta(hours=1)).strftime("%b %d %H:%M")+"Z)"
				buttons.append([{
					"text": tag,
					"callback_data": "whitelist_"+str(user.id)
				}])
	if not len(buttons):
		return rp.Reply(rp.types.ERR_NO_WAITLIST)
	#FIX: Add button to blacklist?
	buttons.append([{
		"text": "Cancel",
		"callback_data": "whitelist_cancel"
	}])
	return rp.Reply(rp.types.WHITELIST_INFO, buttons=buttons)

@requireUser
@requireRank(RANKS.admin)
def whitelist_user(c_user, username):
	user2 = getUserByName(username)
	if user2 == -1:
		return rp.Reply(rp.types.ERR_COLLISION)
	elif user2 is None:
		return rp.Reply(rp.types.ERR_NO_USER)

	try:
		db.getWhitelistedUser(user2.id)
		return rp.Reply(rp.types.ERR_ALREADY_WHITELISTED)
	except KeyError as e:
		db.addWhitelistedUser(user2.id)
	logging.info("%s was whitelisted by %s", user2, c_user)
	# FIX: if user2.left is not None or something like that.
	if user2.rank > RANKS.banned:
		_push_system_message(rp.Reply(rp.types.CUSTOM, text="<i>You have been whitelisted!\nPlease use</i> /start <i>to start the bot.</i>"),who=user2)
	return rp.Reply(rp.types.SUCCESS)

@requireUser
@requireRank(RANKS.admin)
def unwhitelist_user(c_user, username):
	user2 = getUserByName(username)
	if user2 == -1:
		return rp.Reply(rp.types.ERR_COLLISION)
	elif user2 is None:
		return rp.Reply(rp.types.ERR_NO_USER)

	if user2.rank >= c_user.rank:
		return rp.Reply(rp.types.CUSTOM, text="<i>You cannot remove someone of the same rank or higher from the whitelist.</i>")
	try:
		db.getWhitelistedUser(user2.id)
		db.addWhitelistedUser(user2.id, toWhitelist=False)
	except KeyError as e:
		return rp.Reply(rp.types.ERR_NOTHING_TO_DO)
	logging.info("%s was unwhitelisted by %s", user2, c_user)
	return rp.Reply(rp.types.SUCCESS)

@requireUser
@requireRank(RANKS.admin)
def whitelist_reply(call):
  logging.info(str(call))
	#FIX: Return message

@requireUser
@requireRank(RANKS.mod)
def blacklist_user(user, username, reason, msid=None, text=""):
	if isTooSensitive(username, user):
		return rp.Reply(rp.types.ERR_ADMIN_SEARCH)

	user2 = getUserByName(username)
	if user2 == -1:
		return rp.Reply(rp.types.ERR_COLLISION)

	if user2 is None:
		if username.startswith("##"):
			username = username[2:]
		if re.search("^[0-9+]{5,}$",username) is not None:
			try:
				db.getBlacklistedUser(username)
				return rp.Reply(rp.types.ERR_ALREADY_BLACKLISTED)
			except KeyError as ex:
				db.addBlacklistedUser(username)
				return rp.Reply(rp.types.SUCCESS)
		return rp.Reply(rp.types.ERR_NO_USER)

	if user2.rank >= user.rank:
		return rp.Reply(rp.types.CUSTOM, text="<i>You cannot ban someone who is the same rank or higher.</i>")
	if user2.rank == RANKS.banned:
		return rp.Reply(rp.types.ERR_ALREADY_BLACKLISTED)
	
	Sender.stop_invoked(user2, True) # do this before queueing new messages below
	if not user2.left:
		_push_system_message(
			rp.Reply(rp.types.ERR_BLACKLISTED, reason=reason, contact=blacklist_contact),
			who=user2)#, reply_to=msid)
	with db.modifyUser(id=user2.id) as user2:
		user2.setBlacklisted(reason)
		db.addBlacklistedUser(user2.id)
	logging.info("%s was blacklisted by %s for: %s", user2, user, reason)
	if msid is not None:
		Sender.delete(msid, user.id)
		logging.info("Deleted: %s", text)
		if user2 is not None:
			return get_info_mod(user, user2.id)
	return rp.Reply(rp.types.SUCCESS)

@requireUser
@requireRank(RANKS.admin)
def unblacklist_user(user, username):
	if isTooSensitive(username, user):
		return rp.Reply(rp.types.ERR_ADMIN_SEARCH)

	user2 = getUserByName(username)
	if user2 == -1:
		return rp.Reply(rp.types.ERR_COLLISION)
	
	if user2 is None:
		if username.startswith("##"):
			username = username[2:]
		if re.search("^[0-9+]{5,}$",username) is not None:
			try:
				db.getBlacklistedUser(username)
				db.addBlacklistedUser(username, toBlacklist=False)
				return rp.Reply(rp.types.SUCCESS)
			except KeyError as e:
				return rp.Reply(rp.types.ERR_NOTHING_TO_DO)
		return rp.Reply(rp.types.ERR_NO_USER)

	with db.modifyUser(id=user2.id) as user2:
		if user2.rank != RANKS.banned:
			return rp.Reply(rp.types.ERR_NOT_BLACKLISTED)
		user2.setBlacklisted(toBlacklist=False)
		db.addBlacklistedUser(user2.id, toBlacklist=False)
		#FIX: Anything about warnings and stuff? Might need to reduce if too high
	logging.info("%s was unblacklisted by %s", user2, user)
	return rp.Reply(rp.types.SUCCESS)

@requireUser
@requireRank(RANKS.admin)
def show_unblacklist(c_user):
	buttons = []
	for user in db.iterateUsers(order_by="left",order_desc=True):
		if user.isBlacklisted():
			# tag = "@"+user.username if user.username else user.realname
			tag = user.getAnonymizedName()
			buttons.append([{
				"text": tag,
				"callback_data": "unblacklist_"+str(user.id)
			}])
	if not len(buttons):
		return rp.Reply(rp.types.ERR_NO_UNBLACKLIST)
	buttons.append([{
		"text": "Cancel",
		"callback_data": "unblacklist_cancel"
	}])
	return rp.Reply(rp.types.UNBLACKLIST_INFO, buttons=buttons)

#FIX: add reply_to demote
@requireUser
@requireRank(RANKS.admin)
def show_demotelist(c_user):
	buttons = []
	for user in db.iterateUsers():
		# if user.rank > RANKS.user and user.id != c_user.id:
		if user.rank > RANKS.user and user.rank < c_user.rank and user.id != c_user.id:
			tag = user.getAnonymizedName()
			if user.rank > RANKS.mod:
				tag += "🌟"
			buttons.append([{
				"text": tag,
				"callback_data": "demote_"+str(user.id)
			}])
	if not len(buttons):
		return rp.Reply(rp.types.ERR_NO_LIST)
	buttons.append([{
		"text": "Cancel",
		"callback_data": "demote_cancel"
	}])
	return rp.Reply(rp.types.DEMOTELIST_INFO, buttons=buttons)

@requireUser
@requireRank(RANKS.mod)
def demote_user(c_user, username):
	if isTooSensitive(username, c_user):
		return rp.Reply(rp.types.ERR_ADMIN_SEARCH)

	user2 = getUserByName(username)
	if user2 == -1:
		return rp.Reply(rp.types.ERR_COLLISION)
	elif user2 is None:
		return rp.Reply(rp.types.ERR_NO_USER)

	if user2.id == c_user.id:
		return rp.Reply(rp.types.CUSTOM, text="<i>You cannot demote yourself.</i>")

	with db.modifyUser(id=user2.id) as user2:
		if user2.rank >= c_user.rank:
			return rp.Reply(rp.types.CUSTOM, text="<i>You can't demote someone of higher or equal rank.</i>")
		user2.rank = RANKS.user
	logging.info("%s was demoted by %s", user2, c_user)
	if not user2.left:
		_push_system_message(rp.Reply(rp.types.DEMOTED), who=user2)
	return rp.Reply(rp.types.SUCCESS)

	
@requireUser
@requireRank(RANKS.mod)
def cleanup_user(c_user, username):
	if isTooSensitive(username, c_user):
		return rp.Reply(rp.types.ERR_ADMIN_SEARCH)

	user2 = getUserByName(username)
	if user2 == -1:
		return rp.Reply(rp.types.ERR_COLLISION)
	elif user2 is None:
		return rp.Reply(rp.types.ERR_NO_USER)

	if user2.id == c_user.id:
		return rp.Reply(rp.types.CUSTOM, text="<i>You cannot clean yourself. &gt;:3</i>")
	if user2.rank > RANKS.banned:
		return rp.Reply(rp.types.CUSTOM, text="<i>That user has not been banned.</i>")

	for msid in ch.allMappings(user2.id):
		Sender.delete(msid, c_user.id)

	logging.info("The posts of %s were cleaned up by %s", user2, c_user)
	return rp.Reply(rp.types.SUCCESS)

	

@requireUser
def give_karma(user, msid):
	cm = ch.getMessage(msid)
	if cm is None or cm.user_id is None:
		return rp.Reply(rp.types.ERR_NOT_IN_CACHE)

	if cm.hasUpvoted(user):
		return rp.Reply(rp.types.ERR_ALREADY_UPVOTED)
	if user.id == cm.user_id:
		return rp.Reply(rp.types.ERR_UPVOTE_OWN_MESSAGE)

	user = db.getUser(id=user.id)
	user2 = db.getUser(id=cm.user_id)

	if cm.locked:
		return rp.Reply(rp.types.CUSTOM, text="<i>This message has been locked by mods.</i>")
	if not user2.muzzled and not user.muzzled:
		cm.addUpvote(user)
		with db.modifyUser(id=cm.user_id) as user2:
			user2.karma += KARMA_PLUS_ONE
		if not user2.hideKarma and not user2.left:
			_push_system_message(rp.Reply(rp.types.KARMA_NOTIFICATION), who=user2, reply_to=msid)

	logging.info("%s gave %s +1 karma.",user, user2)
	return rp.Reply(rp.types.KARMA_THANK_YOU)

@requireUser
@requireRank(RANKS.admin)
def reset_karma(c_user, username):
	if isTooSensitive(username, c_user):
		return rp.Reply(rp.types.ERR_ADMIN_SEARCH)

	user2 = getUserByName(username)
	if user2 == -1:
		return rp.Reply(rp.types.ERR_COLLISION)
	elif user2 is None:
		return rp.Reply(rp.types.ERR_NO_USER)

	if user2.id == c_user.id and user2.karma > 0:
		return rp.Reply(rp.types.CUSTOM, text="<i>You cannot eliminate your own karma. &gt;:3</i>")
	if user2.rank > RANKS.user and user2.karma > 0:
		return rp.Reply(rp.types.CUSTOM, text="<i>Mods have karmic protection.</i>")

	with db.modifyUser(id=user2.id) as user2:
		user2.karma = 0
		logging.info("%s reset the karma of %s", c_user, user2)
	return rp.Reply(rp.types.CUSTOM, text="<i>This user's karma was reset.</i>")

@requireUser
@requireRank(RANKS.mod)
def engage_lockdown(user, arg=None):
	global lockdown
	if whitelist:
		return rp.Reply(rp.types.CUSTOM, text="<i>No need for a lockdown. The whitelist is already active.</i>")
	if arg is not None and (arg.startswith("off") or arg.startswith("no") or arg.startswith("over") or arg.startswith("end")):
		if not lockdown:
			return rp.Reply(rp.types.CUSTOM, text="<i>There is no lockdown active.</i>")
		logging.info("%s has ended the lockdown.", user);
		lockdown = False
		message= rp.Reply(rp.types.CUSTOM, text="<i>The lockdown is now over.</i>")
		for admin in db.iterateAdmins():
			if admin.id != user.id and not admin.left:
				_push_system_message(message, who=admin)
		return message
	if lockdown:
		return rp.Reply(rp.types.CUSTOM, text="<i>A lockdown is already active.</i>")
	logging.info("%s has engaged a lockdown.", user);
	lockdown = True

	message = rp.Reply(rp.types.CUSTOM, text="<i>You are now in lockdown mode, and the whitelist has been enabled. Use</i> <code>/lockdown off</code> <i>to return to normal.</i>")
	for admin in db.iterateAdmins():
		if admin.id != user.id and not admin.left:
			_push_system_message(message, who=admin)
	return message

@requireUser
@requireRank(RANKS.mod)
def lock_message(c_user, msid, text="media"):
	cm = ch.getMessage(msid)
	if cm is None or cm.user_id is None:
		return rp.Reply(rp.types.ERR_NOT_IN_CACHE)

	user2 = db.getUser(id=cm.user_id)

	if text is None:
		text = "media"

	if not cm.locked:
		cm.locked = True
	else:
		return rp.Reply(rp.types.CUSTOM, text="<i>This message has already been locked.</i>")
	logging.info("%s locked a message from %s%s", c_user, user2, "\nMessage: " + text or "")

	return rp.Reply(rp.types.CUSTOM, text="<i>This message was locked</i>")

@requireUser
@requireRank(RANKS.mod)
def unlock_message(c_user, msid):
	cm = ch.getMessage(msid)
	if cm is None or cm.user_id is None:
		return rp.Reply(rp.types.ERR_NOT_IN_CACHE)

	user2 = db.getUser(id=cm.user_id)

	if cm.locked:
		cm.locked = False
	else:
		return rp.Reply(rp.types.CUSTOM, text="<i>This message wasn't locked.</i>")
	logging.info("%s unlocked a message from %s", c_user, user2)

	return rp.Reply(rp.types.CUSTOM, text="<i>This message was unlocked</i>")

@requireUser
@requireRank(RANKS.admin)
def muzzle_user(c_user, username, toMuzzle=True):
	if isTooSensitive(username, c_user):
		return rp.Reply(rp.types.ERR_ADMIN_SEARCH)

	user2 = getUserByName(username)
	if user2 == -1:
		return rp.Reply(rp.types.ERR_COLLISION)
	elif user2 is None:
		return rp.Reply(rp.types.ERR_NO_USER)

	if user2.muzzled and toMuzzle:
		return rp.Reply(rp.types.CUSTOM, text="<i>That user is already muzzled.</i>")
	if user2.id == c_user.id:
		return rp.Reply(rp.types.CUSTOM, text="<i>You cannot muzzle yourself. &gt;:3</i>")
	if user2.rank > RANKS.user:
		return rp.Reply(rp.types.CUSTOM, text="<i>You cannot muzzle mods.</i>")

	with db.modifyUser(id=user2.id) as user2:
		user2.muzzled = toMuzzle
		logging.info("%s %smuzzled %s", c_user, "" if toMuzzle else "un", user2)
	return rp.Reply(rp.types.CUSTOM, text="<i>This user was "+("" if toMuzzle else "un")+"muzzled</i>")

@requireUser
def expose_to_user(c_user, msid, username):
	if not enable_expose:
		return rp.Reply(rp.types.ERR_COMMAND_DISABLED)

	c_user = db.getUser(id=c_user.id)
	user2 = None

	if msid is not None:
		if not username.startswith("yes"):
			return rp.Reply(rp.types.ERR_EXPOSE_CONFIRM)
		cm = ch.getMessage(msid)
		if cm is None or cm.user_id is None:
			return rp.Reply(rp.types.CUSTOM, text="<i>You can't expose yourself through a system message.</i>") # FIX: can I add cm.user_id to polls?
		if cm.locked:
			return rp.Reply(rp.types.CUSTOM, text="<i>This message has been locked by mods.</i>")
		user2 = db.getUser(id=cm.user_id)
	else:
		if username == "yes":
			return rp.Reply(rp.types.ERR_NO_REPLY)
		if isTooSensitive(username, c_user):
			return rp.Reply(rp.types.ERR_ADMIN_SEARCH)
		user2 = getUserByName(username)
		if user2 == -1:
			return rp.Reply(rp.types.ERR_COLLISION)
	
	if user2 is None:
		return rp.Reply(rp.types.ERR_NO_USER)

	user = {
		"name": c_user.getAnonymizedName(),
		"link":c_user.getIdLink(c_user.getFormattedName())
	}
	logging.info("%s has revealed theirself to %s", c_user, user2)
	if user2.left:
		logging.info("(But it failed because they were away)")
		return rp.Reply(rp.types.CUSTOM, text="<i>Sorry, anon is away.</i>")

	if not user2.muzzled and not c_user.muzzled:
		_push_system_message(rp.Reply(rp.types.EXPOSE_TO,**user), who=user2)
	return rp.Reply(rp.types.EXPOSED)

@requireUser
def prepare_user_message(user: User, msg_score, *, is_media=False, expose=False, tripcode=False):
	# prerequisites
	if user.isInCooldown():
		return rp.Reply(rp.types.ERR_COOLDOWN, until=user.cooldownUntil)
	if expose and not enable_expose:
		return rp.Reply(rp.types.ERR_COMMAND_DISABLED)
	if tripcode and user.tripcode is None:
		return rp.Reply(rp.types.ERR_NO_TRIPCODE)
	if is_media and user.rank < RANKS.mod and media_limit_period is not None:
		if (datetime.now() - user.joined) < media_limit_period:
			return rp.Reply(rp.types.ERR_MEDIA_LIMIT)

	ok = spam_scores.increaseSpamScore(user.id, msg_score)
	if not ok:
		return rp.Reply(rp.types.ERR_SPAMMY)

	return ch.assignMessageId(CachedMessage(user.id))

# who is None -> to everyone except the user <except_who> (if applicable)
# who is not None -> only to the user <who>
# reply_to: msid the message is in reply to
def _push_system_message(m, *, who=None, except_who=None, reply_to=None):
	msid = None
	if who is None: # we only need an ID if multiple people can see the msg
		msid = ch.assignMessageId(CachedMessage())
	Sender.reply(m, msid, who, except_who, reply_to)

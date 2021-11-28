import logging
import os
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from random import randint
from threading import RLock

from src.globals import *

# what's inside the db

class SystemConfig():
	def __init__(self):
		self.motd = None
		self.help = None
	def defaults(self):
		self.motd = ""
		self.help = ""

USER_PROPS = (
	"id", "username", "realname", "rank", "joined", "left", "lastActive",
	"cooldownUntil", "blacklistReason", "warnings", "warnExpiry", "forwardWarned", "karma",
	"hideKarma", "debugEnabled", "tripcode", "tripname", "triphash", "salt", "tripcodeToggle"
)

class User():
	__slots__ = USER_PROPS
	def __init__(self):
		self.id = None # int
		self.username = None # str?
		self.realname = None # str
		self.rank = None # int
		self.joined = None # datetime
		self.left = None # datetime?
		self.lastActive = None # datetime
		self.cooldownUntil = None # datetime?
		self.blacklistReason = None # str?
		self.warnings = None # int
		self.warnExpiry = None # datetime?
		self.forwardWarned = None # bool
		self.karma = None # int
		self.hideKarma = None # bool
		self.debugEnabled = None # bool
		self.tripcode = None # str?
		self.tripname = None # str?
		self.triphash = None # str?
		self.salt = None # str?
		self.tripcodeToggle = None # bool
	def __eq__(self, other):
		if isinstance(other, User):
			return self.id == other.id
		return NotImplemented
	def __str__(self):
		return "%r (%d)" % (self.getFormattedName(), self.id)
	def defaults(self):
		self.rank = RANKS.user
		self.joined = datetime.now()
		self.lastActive = self.joined
		self.warnings = 0
		self.forwardWarned = False #FIX: not yet implemented
		self.karma = 0
		self.hideKarma = False
		self.debugEnabled = False
		self.salt = str(randint(1000,9999))#currently unused
		self.tripcodeToggle = False
	def isJoined(self):
		return self.left is None
	def isInCooldown(self):
		return self.cooldownUntil is not None and self.cooldownUntil >= datetime.now()
	def isBlacklisted(self):
		return self.rank < 0
	def getObfuscatedId(self):
		salt = date.today().toordinal()
		if salt & 0xff == 0: salt >>= 8 # zero bits are bad for hashing
		value = (self.id * salt) & 0xffffff
		alpha = "0123456789abcdefghijklmnopqrstuv"
		return ''.join(alpha[n%32] for n in (value, value>>5, value>>10, value>>15))
	def getObfuscatedKarma(self):
		offset = round(abs(self.karma * 0.2) + 2)
		return self.karma + randint(0, offset + 1) - offset
	def getIdLink(self, text=None):
		return "<a href=\"tg://user?id="+str(self.id)+"\">"+(text.replace("<","&lt;").replace(">","&gt;") if text else str(self.id))+"</a>"
	def getFormattedName(self):
		if self.username is not None:
			return "@" + self.username
		return self.realname or "anon"
	def getAnonymizedName(self):
		tag = self.getObfuscatedId()
		if self.tripcode:
			tag = self.tripname + self.triphash
		return tag
	def getMessagePriority(self):
		inactive_min = (datetime.now() - self.lastActive) / timedelta(minutes=1)
		c1 = max(RANKS.values()) - max(self.rank, 0)
		c2 = int(inactive_min) & 0xffff
		# lower value means higher priority
		# in this case: prioritize by higher rank, then by lower inactivity time
		return c1 << 16 | c2
	def setLeft(self, v=True):
		self.left = datetime.now() if v else None
	def setBlacklisted(self, reason="", toBlacklist=True):
		self.setLeft()
		self.blacklistReason = reason
		if toBlacklist:
			self.rank = RANKS.banned
		else:
			self.rank = RANKS.user
	def addWarning(self):
		if self.warnings < len(COOLDOWN_TIME_BEGIN):
			cooldownTime = COOLDOWN_TIME_BEGIN[self.warnings]
		else:
			x = self.warnings - len(COOLDOWN_TIME_BEGIN)
			cooldownTime = COOLDOWN_TIME_LINEAR_M * x + COOLDOWN_TIME_LINEAR_B
		cooldownTime = timedelta(minutes=cooldownTime)
		self.cooldownUntil = datetime.now() + cooldownTime
		self.warnings += 1
		self.warnExpiry = datetime.now() + timedelta(hours=WARN_EXPIRE_HOURS)
		return cooldownTime
	def removeWarning(self):
		self.warnings = max(self.warnings - 1, 0)
		if self.warnings > 0:
			self.warnExpiry = datetime.now() + timedelta(hours=WARN_EXPIRE_HOURS)
		else:
			self.warnExpiry = None		

# abstract db

class ModificationContext():
	def __init__(self, obj, func, lock=None):
		self.obj = obj
		self.func = func
		self.lock = lock
		if self.lock is not None:
			self.lock.acquire()
	def __enter__(self):
		return self.obj
	def __exit__(self, exc_type, *_):
		if exc_type is None:
			self.func(self.obj)
		if self.lock is not None:
			self.lock.release()

class Database():
	def __init__(self):
		self.lock = RLock()
		assert self.__class__ != Database # do not instantiate directly
	def register_tasks(self, sched):
		raise NotImplementedError()
	def close(self):
		raise NotImplementedError()
	def getUser(self, id=None):
		raise NotImplementedError()
	def setUser(self, id, user):
		raise NotImplementedError()
	def addUser(self, user):
		raise NotImplementedError()
	def iterateUserIds(self):
		raise NotImplementedError()
	def getSystemConfig(self):
		raise NotImplementedError()
	def setSystemConfig(self, config):
		raise NotImplementedError()
	def iterateUsers(self, order_by=None, order_desc=False):
		with self.lock:
			l = list(self.getUser(id=id) for id in self.iterateUserIds(order_by, order_desc))
		yield from l
	def iterateAdmins(self):
		with self.lock:
			l = list(self.getUser(id=id) for id in self.iterateAdmins())
		yield from l
	def modifyUser(self, **kwargs):
		with self.lock:
			user = self.getUser(**kwargs)
			callback = lambda newuser: self.setUser(user.id, newuser)
			return ModificationContext(user, callback, self.lock)
	def addWhitelistedUser(self, **kwargs):
		with self.lock:
			self.addWhitelistedUser(**kwargs)
	def getWhitelistedUser(self, **kwargs):
		with self.lock:
			success = self.getWhitelistedUser(**kwargs)
			return success
	def modifySystemConfig(self):
		with self.lock:
			config = self.getSystemConfig()
			callback = lambda newconfig: self.setSystemConfig(newconfig)
			return ModificationContext(config, callback, self.lock)

# JSON implementation

class JSONDatabase(Database):
	def __init__(self, path):
		super(JSONDatabase, self).__init__()
		self.path = path
		self.db = {"systemConfig": None, "users": []}
		try:
			self._load()
		except FileNotFoundError as e:
			pass
		logging.warning("The JSON backend is meant for development only!")
	def register_tasks(self, sched):
		return
	def close(self):
		return
	@staticmethod
	def _systemConfigToDict(config):
		return {"motd": config.motd, "help": config.help}
	@staticmethod
	def _systemConfigFromDict(d):
		if d is None: return None
		config = SystemConfig()
		config.motd = d["motd"]
		config.help = d["help"] if "help" in d.keys() else ""
		return config
	@staticmethod
	def _userToDict(user):
		props = ["id", "username", "realname", "rank", "joined", "left",
			"lastActive", "cooldownUntil", "blacklistReason", "warnings",
			"warnExpiry", "forwardWarned", "karma", "hideKarma", "debugEnabled", "tripcode","tripname","triphash","salt", "tripcodeToggle"]
		d = {}
		for prop in props:
			value = getattr(user, prop)
			if isinstance(value, datetime):
				value = int(value.replace(tzinfo=timezone.utc).timestamp())
			d[prop] = value
		return d
	@staticmethod
	def _userFromDict(d):
		if d is None: return None
		props = ["id", "username", "realname", "rank", "blacklistReason",
			"warnings", "karma", "hideKarma", "debugEnabled", "tripcodeToggle"]
		props_d = [("tripcode", None),("tripname", None),("triphash", None),("tripcodeToggle",False)]
		dateprops = ["joined", "left", "lastActive", "cooldownUntil", "warnExpiry"]
		user = User()
		for prop in props:
			setattr(user, prop, d[prop])
		for prop, default in props_d:
			setattr(user, prop, d.get(prop, default))
		for prop in dateprops:
			if d[prop] is not None:
				setattr(user, prop, datetime.utcfromtimestamp(d[prop]))
		return user
	def _load(self):
		with self.lock:
			with open(self.path, "r") as f:
				self.db = json.load(f)
	def _save(self):
		with self.lock:
			with open(self.path + "~", "w") as f:
				json.dump(self.db, f)
			os.replace(self.path + "~", self.path)
	def getUser(self, id=None):
		if id is None:
			raise ValueError()
		with self.lock:
			gen = (u for u in self.db["users"] if u["id"] == id)
			try:
				return JSONDatabase._userFromDict(next(gen))
			except StopIteration as e:
				raise KeyError()
	def setUser(self, id, newuser):
		newuser = JSONDatabase._userToDict(newuser)
		with self.lock:
			for i, user in enumerate(self.db["users"]):
				if user["id"] == id:
					self.db["users"][i] = newuser
					self._save()
					return
	def addUser(self, newuser):
		newuser = JSONDatabase._userToDict(newuser)
		with self.lock:
			self.db["users"].append(newuser)
			self._save()
	def iterateUserIds(self):
		with self.lock:
			l = list(u["id"] for u in self.db["users"])
		yield from l
	def getSystemConfig(self):
		with self.lock:
			return JSONDatabase._systemConfigFromDict(self.db["systemConfig"])
	def setSystemConfig(self, config):
		with self.lock:
			self.db["systemConfig"] = JSONDatabase._systemConfigToDict(config)
			self._save()

# SQLite implementation

class SQLiteDatabase(Database):
	def __init__(self, path):
		super(SQLiteDatabase, self).__init__()
		self.db = sqlite3.connect(path, check_same_thread=False,
			detect_types=sqlite3.PARSE_DECLTYPES|sqlite3.PARSE_COLNAMES)
		self.db.row_factory = sqlite3.Row
		self._ensure_schema()
	def register_tasks(self, sched):
		def f():
			with self.lock:
				self.db.commit()
		sched.register(f, seconds=5)
	def close(self):
		with self.lock:
			self.db.commit()
			self.db.close()
	@staticmethod
	def _systemConfigToDict(config):
		return {"motd": config.motd, "help": config.help}
	@staticmethod
	def _systemConfigFromDict(d):
		if len(d) == 0: return None
		config = SystemConfig()
		config.motd = d["motd"]
		config.help = d["help"] if "help" in d.keys() else ""
		return config
	@staticmethod
	def _userToDict(user):
		return {prop: getattr(user, prop) for prop in USER_PROPS}
	@staticmethod
	def _userFromRow(r):
		user = User()
		for prop in r.keys():
			setattr(user, prop, r[prop])
		return user
	def _ensure_schema(self):
		def row_exists(table, name):
			cur = self.db.execute("PRAGMA table_info(`" + table + "`);")
			return any(row[1] == name for row in cur)

		with self.lock:
			# create initial schema
			self.db.execute("""
CREATE TABLE IF NOT EXISTS `system_config` (
	`name` TEXT NOT NULL,
	`value` TEXT NOT NULL,
	PRIMARY KEY (`name`)
);
			""".strip())
			self.db.execute("""
CREATE TABLE IF NOT EXISTS `whitelist` (
	`id` BIGINT NOT NULL,
	PRIMARY KEY (`id`)
);
			""".strip())
			self.db.execute("""
CREATE TABLE IF NOT EXISTS `blacklist` (
	`id` BIGINT NOT NULL,
	PRIMARY KEY (`id`)
);
			""".strip())
			self.db.execute("""
CREATE TABLE IF NOT EXISTS `users` (
	`id` BIGINT NOT NULL,
	`username` TEXT,
	`realname` TEXT NOT NULL,
	`rank` INTEGER NOT NULL,
	`joined` TIMESTAMP NOT NULL,
	`left` TIMESTAMP,
	`lastActive` TIMESTAMP NOT NULL,
	`cooldownUntil` TIMESTAMP,
	`blacklistReason` TEXT,
	`warnings` INTEGER NOT NULL,
	`warnExpiry` TIMESTAMP,
	`forwardWarned` TINYINT,
	`karma` INTEGER NOT NULL,
	`hideKarma` TINYINT NOT NULL,
	`debugEnabled` TINYINT NOT NULL,
	`tripcode` TEXT,
	`tripname` TEXT,
	`triphash` TEXT,
	`salt` TEXT,
	`tripcodeToggle` TINYINT NOT NULL,
	PRIMARY KEY (`id`)
);
			""".strip())
			# migration
			if not row_exists("users", "tripcode"):
				self.db.execute("ALTER TABLE `users` ADD `tripcode` TEXT")
			if not row_exists("users", "tripname"):
				self.db.execute("ALTER TABLE `users` ADD `tripname` TEXT")
			if not row_exists("users", "triphash"):
				self.db.execute("ALTER TABLE `users` ADD `triphash` TEXT")
			if not row_exists("users", "salt"):
				self.db.execute("ALTER TABLE `users` ADD `salt` TEXT")
			if not row_exists("users", "tripcodeToggle"):
				self.db.execute("ALTER TABLE `users` ADD `tripcodeToggle` TINYINT") # FIX: Look up SQL to default to true, test it.
			# These turned out not to be necessary, the bot strips forwards easily.
			# if not row_exists("users", "forwardWarned"):
			# 	self.db.execute("ALTER TABLE `users` ADD `forwardWarned` TINYINT")
	def getUser(self, id=None):
		if id is None:
			raise ValueError()
		sql = "SELECT * FROM users WHERE id = ?"
		param = id
		with self.lock:
			cur = self.db.execute(sql, (param, ))
			row = cur.fetchone()
		if row is None:
			raise KeyError()
		return SQLiteDatabase._userFromRow(row)
	def setUser(self, id, newuser):
		newuser = SQLiteDatabase._userToDict(newuser)
		del newuser['id'] # this is our primary key
		sql = "UPDATE users SET "
		sql += ", ".join("`%s` = ?" % k for k in newuser.keys())
		sql += " WHERE id = ?"
		param = list(newuser.values()) + [id, ]
		with self.lock:
			self.db.execute(sql, param)
	def addUser(self, newuser):
		newuser = SQLiteDatabase._userToDict(newuser)
		sql = "INSERT INTO users("
		sql += ", ".join("`%s`" % k for k in newuser.keys())
		sql += ") VALUES ("
		sql += ", ".join("?" for i in range(len(newuser)))
		sql += ")"
		param = list(newuser.values())
		with self.lock:
			self.db.execute(sql, param)
	def addWhitelistedUser(self, id=None, toWhitelist=True): #if a username had been added, it was converted into an ID before coming here.
		if id is None:
			raise ValueError()
		if toWhitelist:
			sql = "INSERT INTO whitelist(id) VALUES (?)"
		else:
			sql = "DELETE FROM whitelist WHERE id = ?"
		param = str(id).strip().lower()
		with self.lock:
			self.db.execute(sql, (param, ))
	def getWhitelistedUser(self, id=None):
		if id is None:
			raise ValueError()
		sql = "SELECT id FROM whitelist WHERE id = ?"
		param = str(id).strip().lower()
		with self.lock:
			cur = self.db.execute(sql, (param, ))
			row = cur.fetchone()
		if row is None:
			raise KeyError()
		return True
	def addBlacklistedUser(self, id=None, toBlacklist=True): #if a username had been added, it was converted into an ID before coming here.
		if id is None:
			raise ValueError()
		if toBlacklist:
			sql = "INSERT INTO blacklist(id) VALUES (?)"
		else:
			sql = "DELETE FROM blacklist WHERE id = ?"
		param = str(id).strip().lower()
		with self.lock:
			self.db.execute(sql, (param, ))
	def getBlacklistedUser(self, id=None):
		if id is None:
			raise ValueError()
		sql = "SELECT id FROM blacklist WHERE id = ?"
		param = str(id).strip().lower()
		with self.lock:
			cur = self.db.execute(sql, (param, ))
			row = cur.fetchone()
		if row is None:
			raise KeyError()
		return True
	def iterateUserIds(self, order_by=None, order_desc=False):
		sql = "SELECT `id` FROM users"
		if order_by:
			sql += " ORDER BY ?" + (" DESC" if order_desc else "")
		with self.lock:
			if order_by:
				cur = self.db.execute(sql, (str(order_by), ))
			else:
				cur = self.db.execute(sql)
			l = cur.fetchall()
		yield from l
	def iterateUsers(self, order_by=None, order_desc=False):
		sql = "SELECT * FROM users"
		if order_by:
			sql += " ORDER BY ?" + (" DESC" if order_desc else "")
		with self.lock:
			if order_by:
				cur = self.db.execute(sql, (str(order_by), ))
			else:
				cur = self.db.execute(sql)
			l = list(SQLiteDatabase._userFromRow(row) for row in cur)
		yield from l
	def iterateAdmins(self):
		sql = "SELECT * FROM users WHERE rank >= ?"
		param = RANKS.admin
		with self.lock:
			cur = self.db.execute(sql, (param, ))
			l = list(SQLiteDatabase._userFromRow(row) for row in cur)
		yield from l
	def getSystemConfig(self):
		sql = "SELECT * FROM system_config"
		with self.lock:
			cur = self.db.execute(sql)
			d = {row['name']: row['value'] for row in cur}
		return SQLiteDatabase._systemConfigFromDict(d)
	def setSystemConfig(self, config):
		d = SQLiteDatabase._systemConfigToDict(config)
		sql = "REPLACE INTO system_config(`name`, `value`) VALUES (?, ?)"
		with self.lock:
			for k, v in d.items():
				self.db.execute(sql, (k, v))

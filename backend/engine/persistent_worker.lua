-- 统一常驻 PoB2 LuaJIT Worker（Mac / Windows / Linux 跨平台）
-- 合并了旧 server.py 的全量 45+ 面板 + lua-utf8 垫片，以及常驻进程架构（单次 ~0.25s）。
--
-- 协议（纯文本行，返回值使用单行 JSON）：
--   PING
--       -> PONG
--   SIM\t<id>\t<xmlPath>\t<lockDpsKey>\t<includeAudit>
--       -> RES_JSON\t<id>\t<json>
--       -> ERR\t<id>\t<message>
--   QUIT
--       -> 退出

io.read = function() return "" end

-- 跨平台 lua-utf8 垫片：macOS / Windows 默认 LuaJIT 不带编译好的 lua-utf8.so，
-- 只有本机没装原生模块时才会落入 preload 兜底。
package.preload["lua-utf8"] = function()
  local u = {}
  for k, v in pairs(string) do u[k] = v end
  u.next = function(s, i)
    i = (i or 1) + 1
    if i <= #s then return i end
  end
  return u
end

local okBoot, bootErr = pcall(function()
  dofile("HeadlessWrapper.lua")
end)

if not okBoot or not __mainObject__ or not __mainObject__.main then
  local msg = tostring(bootErr or "HeadlessWrapper 未初始化 __mainObject__.main"):gsub("[\r\n]+", " ")
  io.stdout:write("BOOT_ERR\t" .. msg .. "\n")
  io.stdout:flush()
  os.exit(1)
end

local function nowMs()
  return os.clock() * 1000
end

local function isInf(v)
  local n = tonumber(v)
  return n == math.huge or n == -math.huge
end

local function safeNum(v)
  local n = tonumber(v)
  if not n or n ~= n or n == math.huge or n == -math.huge then
    return 0
  end
  return n
end

local function minPos(...)
  local best = nil
  for i = 1, select("#", ...) do
    local v = safeNum(select(i, ...))
    if v > 0 and (best == nil or v < best) then
      best = v
    end
  end
  return best or 0
end

local function maxPos(...)
  local best = 0
  for i = 1, select("#", ...) do
    local v = safeNum(select(i, ...))
    if v > best then best = v end
  end
  return best
end

local function readFile(path)
  local f = io.open(path, "rb")
  if not f then return nil end
  local s = f:read("*a")
  f:close()
  return s
end

local function jsonEscape(s)
  return tostring(s or "")
    :gsub("\\", "\\\\")
    :gsub('"', '\\"')
    :gsub("\n", " ")
    :gsub("\r", " ")
    :gsub("\t", " ")
end

local DPS_PRIORITY = {
  "FullDPS",
  "CombinedDPS",
  "TotalDPS",
  "TotalDotDPS",
  "TotalDot",
  "AverageBurstDamage",
  "AverageDamage",
}

local function pickDps(o, lockKey)
  if not o then return 0, "None" end
  if lockKey and lockKey ~= "" and lockKey ~= "-" then
    local v = safeNum(o[lockKey])
    if v > 0 or o[lockKey] ~= nil then
      return v, lockKey
    end
  end
  for _, k in ipairs(DPS_PRIORITY) do
    local v = safeNum(o[k])
    if v > 0 then
      return v, k
    end
  end
  return 0, (lockKey and lockKey ~= "" and lockKey ~= "-") and lockKey or "TotalDPS"
end

local function hasDualWeapons()
  if not build or not build.itemsTab or not build.itemsTab.slots then return false end
  local s1 = build.itemsTab.slots["Weapon 1 Swap"]
  local s2 = build.itemsTab.slots["Weapon 2 Swap"]
  return (s1 and s1.selItemId and s1.selItemId > 0)
      or (s2 and s2.selItemId and s2.selItemId > 0)
      or false
end

local function calcSpiritForWeaponSet(ws)
  if not build or not build.calcsTab or not build.calcsTab.calcs then
    return 0, 0
  end
  local ok, env = pcall(build.calcsTab.calcs.initEnv, build, "MAIN", { weaponSet = ws })
  if not ok or not env then return 0, 0 end
  local ok2 = pcall(build.calcsTab.calcs.perform, env)
  if not ok2 or not env.player or not env.player.output then return 0, 0 end
  local o = env.player.output
  return safeNum(o.Spirit), safeNum(o.SpiritUnreserved)
end

local function collectSpiritAudit()
  local list = {}
  local env = build and build.calcsTab and build.calcsTab.mainEnv
  if not env or not env.player or not env.player.activeSkillList then return list end
  for _, sk in ipairs(env.player.activeSkillList) do
    local resBase = safeNum(sk.skillData and sk.skillData.SpiritReservedBase)
    local resPct = safeNum(sk.skillData and sk.skillData.SpiritReservedPercent)
    if resBase > 0 or resPct > 0 then
      local g = sk.socketGroup
      local name = (sk.activeEffect and sk.activeEffect.grantedEffect and sk.activeEffect.grantedEffect.name)
        or (sk.skillCfg and sk.skillCfg.skillName)
        or "Spirit Skill"
      table.insert(list, {
        name = name,
        reserved = resBase,
        reservedPct = resPct,
        set1 = g and (g.set1 ~= false) or true,
        set2 = g and (g.set2 ~= false) or true,
      })
    end
  end
  return list
end

local function extractOutput(lockKey, includeAudit, elapsedMs)
  if not build or not build.calcsTab or not build.calcsTab.mainOutput then
    return {
      dps = 0, dpsSource = "None", ehp = 0, ehpInfinite = 0,
      life = 0, es = 0, mana = 0,
      spirit = 0, spiritReserved = 0, spiritUnreserved = 0,
      spiritSet1 = 0, spiritUnreservedSet1 = 0, spiritSet2 = 0, spiritUnreservedSet2 = 0,
      activeWeaponSet = 1, hasDualWeaponSet = 0, chaosInoculation = 0,
      armour = 0, evasion = 0, evadeChance = 0, blockChance = 0,
      speed = 0, critChance = 0, critMult = 0, hitChance = 0,
      moveSpeed = 0, skillMoveSpeed = 0,
      fireRes = 0, coldRes = 0, lightningRes = 0, chaosRes = 0,
      fireOverCap = 0, coldOverCap = 0, lightningOverCap = 0, chaosOverCap = 0,
      str = 0, dex = 0, int = 0, reqStr = 0, reqDex = 0, reqInt = 0,
      physMaxHit = 0, eleMaxHit = 0, chaosMaxHit = 0,
      elapsedMs = elapsedMs,
    }, {}
  end

  local o = build.calcsTab.mainOutput
  local env = build.calcsTab.mainEnv
  local activeWs = (env and env.weaponSet) or 1
  local dualWs = hasDualWeapons()

  local dps, dpsSource = pickDps(o, lockKey)
  local life = safeNum(o.Life)
  local es = safeNum(o.EnergyShield)
  local ehpInf = isInf(o.TotalEHP) and 1 or 0
  local rawEhp = safeNum(o.TotalEHP)
  local ehp = (ehpInf == 0 and rawEhp > 0) and rawEhp or (life + es)

  local critMultRaw = safeNum(o.CritMultiplier)
  local critMult = (critMultRaw > 0 and critMultRaw < 20) and (critMultRaw * 100) or critMultRaw
  local msRaw = safeNum(o.EffectiveMovementSpeedMod)
  local moveSpeed = msRaw > 0 and ((msRaw - 1) * 100) or 0
  local skillMsRaw = safeNum(o.MovementSpeedWhileUsingSkill)
  local skillMoveSpeed = skillMsRaw > 0 and ((skillMsRaw - 1) * 100) or moveSpeed
  local evadeChance = maxPos(o.EvadeChance, o.MeleeEvadeChance, o.ProjectileEvadeChance)
  local eleMaxHit = minPos(o.FireMaximumHitTaken, o.ColdMaximumHitTaken, o.LightningMaximumHitTaken)

  local spirit = safeNum(o.Spirit)
  local spiritUnres = safeNum(o.SpiritUnreserved)
  local spiritRes = math.max(0, spirit - spiritUnres)
  local sp1, u1 = spirit, spiritUnres
  local sp2, u2 = 0, 0

  if dualWs or (includeAudit and spiritUnres < 0) then
    if activeWs == 1 then
      sp1, u1 = spirit, spiritUnres
      sp2, u2 = calcSpiritForWeaponSet(2)
    else
      sp2, u2 = spirit, spiritUnres
      sp1, u1 = calcSpiritForWeaponSet(1)
    end
  end

  local auditList = includeAudit and collectSpiritAudit() or {}

  return {
    dps = dps,
    dpsSource = dpsSource,
    ehp = ehp,
    ehpInfinite = ehpInf,
    life = life,
    es = es,
    mana = safeNum(o.Mana),
    spirit = spirit,
    spiritReserved = spiritRes,
    spiritUnreserved = spiritUnres,
    spiritSet1 = sp1,
    spiritUnreservedSet1 = u1,
    spiritSet2 = sp2,
    spiritUnreservedSet2 = u2,
    activeWeaponSet = activeWs,
    hasDualWeaponSet = dualWs and 1 or 0,
    chaosInoculation = o.ChaosInoculation and 1 or 0,
    armour = safeNum(o.Armour),
    evasion = safeNum(o.Evasion),
    evadeChance = evadeChance,
    blockChance = safeNum(o.EffectiveBlockChance),
    speed = safeNum(o.Speed),
    critChance = safeNum(o.CritChance),
    critMult = critMult,
    hitChance = safeNum(o.HitChance),
    moveSpeed = moveSpeed,
    skillMoveSpeed = skillMoveSpeed,
    manaCost = safeNum(o.ManaCost),
    manaPerSecCost = safeNum(o.ManaPerSecondCost),
    manaUnreserved = safeNum(o.ManaUnreserved),
    manaRegen = safeNum(o.ManaRegenRecovery),
    lifeRecovery = safeNum(o.LifeRegenRecovery) + safeNum(o.LifeLeechGainRate),
    esRecovery = safeNum(o.EnergyShieldRegenRecovery) + safeNum(o.EnergyShieldLeechGainRate),
    fireRes = safeNum(o.FireResist),
    coldRes = safeNum(o.ColdResist),
    lightningRes = safeNum(o.LightningResist),
    chaosRes = o.ChaosInoculation and 100 or safeNum(o.ChaosResist),
    fireOverCap = safeNum(o.FireResistOverCap),
    coldOverCap = safeNum(o.ColdResistOverCap),
    lightningOverCap = safeNum(o.LightningResistOverCap),
    chaosOverCap = safeNum(o.ChaosResistOverCap),
    str = safeNum(o.Str),
    dex = safeNum(o.Dex),
    int = safeNum(o.Int),
    reqStr = safeNum(o.ReqStr),
    reqDex = safeNum(o.ReqDex),
    reqInt = safeNum(o.ReqInt),
    physMaxHit = safeNum(o.PhysicalMaximumHitTaken),
    eleMaxHit = eleMaxHit,
    chaosMaxHit = safeNum(o.ChaosMaximumHitTaken),
    elapsedMs = elapsedMs,
  }, auditList
end

local function encodeResultJson(tbl, auditList)
  local parts = {}
  for k, v in pairs(tbl) do
    if type(v) == "string" then
      table.insert(parts, string.format('"%s":"%s"', k, jsonEscape(v)))
    else
      table.insert(parts, string.format('"%s":%.3f', k, safeNum(v)))
    end
  end
  if auditList and #auditList > 0 then
    local items = {}
    for _, a in ipairs(auditList) do
      table.insert(items, string.format(
        '{"name":"%s","reserved":%.1f,"reservedPct":%.1f,"set1":%s,"set2":%s}',
        jsonEscape(a.name), safeNum(a.reserved), safeNum(a.reservedPct),
        a.set1 and "true" or "false", a.set2 and "true" or "false"
      ))
    end
    table.insert(parts, '"spiritAudit":[' .. table.concat(items, ",") .. ']')
  end
  return "{" .. table.concat(parts, ",") .. "}"
end

local function runOne(id, path, lockKey, includeAudit)
  local xml = readFile(path)
  if not xml then
    io.stdout:write("ERR\t" .. id .. "\tmissing xml\n")
    io.stdout:flush()
    return
  end
  local t0 = nowMs()
  local ok, err = pcall(function()
    __mainObject__.main:SetMode("BUILD", false, "Sim", xml, true)
    runCallback("OnFrame")
    runCallback("OnFrame")
  end)
  local dt = nowMs() - t0
  if not ok then
    io.stdout:write("ERR\t" .. id .. "\t" .. tostring(err):gsub("[\r\n]+", " ") .. "\n")
    io.stdout:flush()
    return
  end
  local res, audit = extractOutput(lockKey, includeAudit, dt)
  io.stdout:write("RES_JSON\t" .. id .. "\t" .. encodeResultJson(res, audit) .. "\n")
  io.stdout:flush()
end

io.stdout:write("READY\n")
io.stdout:flush()

while true do
  local line = io.stdin:read("*l")
  if not line then break end
  if line == "QUIT" then break end
  if line == "PING" then
    io.stdout:write("PONG\n")
    io.stdout:flush()
  else
    local cmd, rest = line:match("^(%u+)\t(.*)$")
    if cmd == "SIM" and rest then
      local fields = {}
      for f in (rest .. "\t"):gmatch("(.-)\t") do
        table.insert(fields, f)
      end
      local id = fields[1] or ""
      local path = fields[2] or ""
      local lockKey = fields[3] or "-"
      local includeAudit = (fields[4] == "1")
      if id ~= "" and path ~= "" then
        runOne(id, path, lockKey, includeAudit)
      else
        io.stdout:write("ERR\t" .. id .. "\tinvalid SIM args\n")
        io.stdout:flush()
      end
    elseif cmd then
      io.stdout:write("ERR\t-\tunknown command: " .. tostring(cmd) .. "\n")
      io.stdout:flush()
    end
  end
end

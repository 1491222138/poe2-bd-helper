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

-- Lua 5.1 / LuaJIT 无 LUA52COMPAT 兼容垫片（不修改用户原版 PoB2 目录即可正常运行）
if not table.unpack then table.unpack = unpack end
if not bit32 and bit then
  bit32 = {
    band = bit.band,
    bor = bit.bor,
    bxor = bit.bxor,
    bnot = bit.bnot,
    lshift = bit.lshift,
    rshift = bit.rshift,
    arshift = bit.arshift,
  }
end

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

-- 兼容不同版本 PoB2 HeadlessWrapper：
--   - 新版（含 _SimpleGraphic.def.lua）暴露全局 __mainObject__
--   - v0.23.1 等历史发行版使用局部 mainObject，但 Launch.lua / Main.lua 始终暴露全局 launch 与 main
local mainObj = __mainObject__ or launch
local mainCtrl = (mainObj and mainObj.main) or main
if mainCtrl and mainCtrl.modes and mainCtrl.modes["BUILD"] and not build then
  build = mainCtrl.modes["BUILD"]
end

if not okBoot or not mainCtrl or (mainObj and mainObj.promptMsg) then
  local prompt = mainObj and mainObj.promptMsg
  local msg = tostring(bootErr or prompt or "HeadlessWrapper 未初始化 main 对象"):gsub("[\r\n]+", " ")
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

local function isArrayTable(t)
  if type(t) ~= "table" then return false end
  local maxIdx = 0
  local count = 0
  for k, _ in pairs(t) do
    if type(k) ~= "number" or k < 1 or math.floor(k) ~= k then
      return false
    end
    if k > maxIdx then maxIdx = k end
    count = count + 1
  end
  return maxIdx == count
end

local function encodeJsonValue(v)
  local tv = type(v)
  if v == nil then
    return "null"
  elseif tv == "boolean" then
    return v and "true" or "false"
  elseif tv == "number" then
    local n = safeNum(v)
    if math.floor(n) == n and math.abs(n) < 1e15 then
      return string.format("%d", n)
    end
    return string.format("%.3f", n)
  elseif tv == "string" then
    return '"' .. jsonEscape(v) .. '"'
  elseif tv == "table" then
    if isArrayTable(v) then
      local items = {}
      for i = 1, #v do
        table.insert(items, encodeJsonValue(v[i]))
      end
      return "[" .. table.concat(items, ",") .. "]"
    else
      local items = {}
      for k, val in pairs(v) do
        if type(k) == "string" then
          table.insert(items, '"' .. jsonEscape(k) .. '":' .. encodeJsonValue(val))
        end
      end
      return "{" .. table.concat(items, ",") .. "}"
    end
  end
  return "null"
end

local function calcModMult(sk, statName, outVal)
  local v = safeNum(outVal)
  if v > 0 then return v end
  if sk and sk.skillModList and sk.skillCfg then
    local ok, res = pcall(function()
      local inc = safeNum(sk.skillModList:Sum("INC", sk.skillCfg, statName))
      local more = safeNum(sk.skillModList:More(sk.skillCfg, statName))
      if more <= 0 then more = 1 end
      return math.max(0, (1 + inc / 100) * more)
    end)
    if ok and res and res > 0 then return res end
  end
  return 0
end

local function sumSkillModBase(sk, statName)
  if not sk or not sk.skillModList or not sk.skillCfg then return 0 end
  local ok, res = pcall(function()
    return safeNum(sk.skillModList:Sum("BASE", sk.skillCfg, statName))
  end)
  return (ok and res) or 0
end

local function hasSkillModFlag(sk, flagName)
  if not sk or not sk.skillModList or not sk.skillCfg then return false end
  local ok, res = pcall(function()
    return sk.skillModList:Flag(sk.skillCfg, flagName) and true or false
  end)
  return (ok and res) or false
end

local function hasSkillType(sk, typeName)
  if not sk or not sk.skillTypes or not SkillType then return false end
  local st = SkillType[typeName]
  return (st and sk.skillTypes[st]) and true or false
end

-- PoE2 技能固有物理属性表：PoB2 Lua 不会把技能自带的撞墙弹射/全穿透/同组命中冷却写成 ModFlag，
-- 仅在 PoB2 输出缺失（0/false）时补齐真实游戏引擎固有物理特性，绝不覆盖装备/辅助词条带来的实算值。
local INNATE_SKILL_PHYSICS = {
  ["twister"] = {
    archetype = "roaming_bouncing_proj",
    bouncesOffTerrain = true,
    pierceAll = true,
    hitIntervalSec = 0.66,
    baseDuration = 3.0,
    multiHit = true,
  },
  ["spark"] = {
    archetype = "roaming_bouncing_proj",
    bouncesOffTerrain = true,
    hitIntervalSec = 0.66,
    baseDuration = 2.0,
    multiHit = true,
  },
  ["ball lightning"] = {
    archetype = "roaming_bouncing_proj",
    pierceAll = true,
    hitIntervalSec = 0.15,
    baseDuration = 4.0,
    multiHit = true,
  },
  ["eye of winter"] = {
    archetype = "roaming_bouncing_proj",
    pierceAll = true,
    multiHit = true,
  },
  ["tornado"] = {
    archetype = "roaming_bouncing_proj",
    pierceAll = true,
    hitIntervalSec = 0.25,
    multiHit = true,
  },
  ["ember fusillade"] = { multiHit = true },
  ["tempest bell"] = { hitIntervalSec = 0.30, multiHit = true },
  ["lightning rod"] = { multiHit = true },
  ["earthshatter"] = { multiHit = true },
  ["volcanic fissure"] = { multiHit = true },
  ["firestorm"] = { multiHit = true },
  ["rain of arrows"] = { multiHit = true },
  ["spiral volley"] = { multiHit = true },
}

local function extractSkillMechanics(sk, o, sg)
  local ae = sk and sk.activeEffect
  local ge = ae and ae.grantedEffect
  local src = ae and ae.srcInstance
  local sd = sk and sk.skillData or {}

  local partIdx = safeNum(src and src.skillPart)
  local partName = ""
  local availableParts = {}
  if ge and type(ge.parts) == "table" and #ge.parts > 0 then
    if partIdx <= 0 then partIdx = 1 end
    for i, p in ipairs(ge.parts) do
      local pn = type(p) == "table" and tostring(p.name or ("Part " .. i)) or tostring(p)
      table.insert(availableParts, pn)
      if i == partIdx then
        partName = pn
      end
    end
  end

  local stageCount = safeNum(src and src.skillStageCount)
  if stageCount <= 0 then
    stageCount = maxPos(o and o.StageCount, sumSkillModBase(sk, "Multiplier:Stage"))
  end
  local stageMax = maxPos(
    sd.maxStages,
    sd.stageMax,
    sd.stagesMax,
    o and o.MaxStages,
    sumSkillModBase(sk, "MaxStages")
  )
  if stageMax <= 0 and ge and type(ge.stats) == "table" then
    stageMax = maxPos(ge.stats.max_stages, ge.stats.skill_max_stages)
  end

  local gemCount = safeNum(src and src.count)
  if gemCount <= 0 then gemCount = 1 end
  local includeInFullDPS = (sg and sg.includeInFullDPS == true) and true or false

  local duration = safeNum(o and o.Duration)
  local durationSecondary = safeNum(o and o.DurationSecondary)
  local durationMod = 0
  if duration > 0 or durationSecondary > 0 or hasSkillType(sk, "Duration") or safeNum(o and o.DurationMod) > 0 then
    durationMod = calcModMult(sk, "Duration", o and o.DurationMod)
  end

  local isProj = hasSkillType(sk, "Projectile") or safeNum(o and o.ProjectileCount) > 0 or safeNum(o and o.ProjectileSpeedMod) > 0
  local projSpeedMod = isProj and calcModMult(sk, "ProjectileSpeed", o and o.ProjectileSpeedMod) or 0
  local projCount = safeNum(o and o.ProjectileCount)
  if projCount <= 0 and isProj then
    local extraProj = sumSkillModBase(sk, "ProjectileCount")
    if extraProj > 0 then projCount = 1 + extraProj end
  end
  local pierceCount = maxPos(o and o.PierceCount, isProj and sumSkillModBase(sk, "PierceCount") or 0)
  local pierceChance = safeNum(o and o.PierceChance)
  local pierceAll = hasSkillModFlag(sk, "PierceAllTargets") or (sd.pierceAllTargets == true) or (pierceChance >= 100 and pierceCount <= 0)
  local chainCount = maxPos(o and o.ChainCountMax, o and o.ChainCount, isProj and sumSkillModBase(sk, "ChainCountMax") or 0)
  local terrainChainChance = maxPos(
    o and o.TerrainChainChance,
    isProj and sumSkillModBase(sk, "TerrainChainChance") or 0,
    isProj and sumSkillModBase(sk, "ChainFromTerrainChance") or 0
  )
  local bouncesOffTerrain = (terrainChainChance > 0)
    or hasSkillModFlag(sk, "ChainFromTerrain")
    or hasSkillModFlag(sk, "ProjectileBouncesOffTerrain")
    or (sd.bouncesOffTerrain == true)
    or (sd.bounceOffWalls == true)
  local bounceCount = maxPos(sd.bounceCount, sd.maxBounces, isProj and sumSkillModBase(sk, "BounceCount") or 0)
  local forkCount = maxPos(o and o.ForkCountMax, o and o.ForkCount, isProj and sumSkillModBase(sk, "ForkCountMax") or 0)
  local splitCount = maxPos(o and o.SplitCount, isProj and sumSkillModBase(sk, "SplitCount") or 0)
  local hitIntervalSec = maxPos(sd.hitCooldown, sd.damageInterval, o and o.HitCooldown)

  local aoeRadius = maxPos(o and o.AreaOfEffectRadiusMetres, o and o.AreaOfEffectRadius)
  local isAoe = hasSkillType(sk, "Area") or aoeRadius > 0 or safeNum(o and o.AreaOfEffectMod) > 0
  local aoeMod = isAoe and calcModMult(sk, "AreaOfEffect", o and o.AreaOfEffectMod) or 0

  local sealMax = safeNum(o and o.SealMax)
  local sealCooldown = safeNum(o and o.SealCooldown)
  local timeMaxSeals = safeNum(o and o.TimeMaxSeals)
  local cooldown = safeNum(o and o.Cooldown)
  local cooldownRecovery = calcModMult(sk, "CooldownRecovery", o and o.CooldownRecovery)
  local triggerRate = maxPos(o and o.SkillTriggerRate_used, o and o.EffectiveTriggerRate, o and o.TriggerRateCap)
  local repeatCount = maxPos(o and o.RepeatCount, sumSkillModBase(sk, "RepeatCount"))

  local skNameLow = tostring((ge and ge.name) or ""):lower()
  local innate = INNATE_SKILL_PHYSICS[skNameLow]
  if innate then
    if innate.bouncesOffTerrain and not bouncesOffTerrain then
      bouncesOffTerrain = true
    end
    if innate.pierceAll and not pierceAll then
      pierceAll = true
    end
    if innate.hitIntervalSec and hitIntervalSec <= 0 then
      hitIntervalSec = innate.hitIntervalSec
    end
    if innate.baseDuration and duration <= 0 then
      local dm = (durationMod > 0) and durationMod or calcModMult(sk, "Duration", o and o.DurationMod)
      if dm <= 0 then dm = 1 end
      durationMod = dm
      duration = innate.baseDuration * dm
    end
  end

  local isMelee = hasSkillType(sk, "Melee")
  local isSlam = hasSkillType(sk, "Slam") or (skNameLow:find("slam", 1, true) ~= nil)
  local isChannelling = hasSkillType(sk, "Channel") or hasSkillType(sk, "Channelling")
  local isTotem = hasSkillType(sk, "Totem") or hasSkillType(sk, "SummonsTotem") or safeNum(o and o.ActiveTotemLimit) > 0
  local isMinion = hasSkillType(sk, "Minion") or hasSkillType(sk, "CreatesMinion") or safeNum(o and o.ActiveMinionLimit) > 0
  local isTriggered = hasSkillType(sk, "Triggered") or (triggerRate > 0)
  local isCrossbow = hasSkillType(sk, "CrossbowAmmoSkill") or hasSkillType(sk, "CrossbowSkill")
    or safeNum(o and o.ReloadTime) > 0 or safeNum(sd.reloadTime) > 0 or safeNum(o and o.MagazineSize) > 0
  local isGrenade = hasSkillType(sk, "Grenade") or (skNameLow:find("grenade", 1, true) ~= nil)
  local isShapeshift = hasSkillType(sk, "Shapeshift") or hasSkillType(sk, "Bear") or hasSkillType(sk, "Wolf") or hasSkillType(sk, "Wyvern")
    or (skNameLow:find("demon form", 1, true) ~= nil)

  local totalDot = safeNum(o and o.TotalDotDPS)
  local combDps = safeNum(o and o.CombinedDPS)
  local dotSharePct = (totalDot > 0 and combDps > 0) and math.min(100, (totalDot / combDps) * 100) or 0
  local isDot = (dotSharePct >= 40) or (totalDot > 0 and combDps <= 0) or hasSkillType(sk, "DamageOverTime")

  local archetype = "aoe_spell_attack"
  if isTriggered and triggerRate > 0 then
    archetype = "trigger_meta"
  elseif isMinion then
    archetype = "minion"
  elseif isTotem then
    archetype = "totem"
  elseif isCrossbow or isGrenade then
    archetype = "crossbow_grenade"
  elseif isSlam then
    archetype = "slam_warcry"
  elseif isChannelling or stageMax > 1 then
    archetype = "channel_stage"
  elseif isDot then
    archetype = "dot_ailment"
  elseif innate and innate.archetype then
    archetype = innate.archetype
  elseif isProj and (bouncesOffTerrain or (duration > 0 and (pierceAll or hitIntervalSec > 0))) then
    archetype = "roaming_bouncing_proj"
  elseif isProj then
    archetype = "direct_proj"
  elseif isMelee then
    archetype = "melee_strike"
  end

  local avgHitVal = safeNum(o and o.AverageHit)
  if avgHitVal <= 0 then avgHitVal = safeNum(o and o.AverageDamage) end
  local spdValForCheck = safeNum(o and o.Speed)
  local pobSingleHitOnly = false
  if gemCount <= 1 and avgHitVal > 0 and spdValForCheck > 0 and combDps > 0 then
    local singleEst = avgHitVal * spdValForCheck
    if math.abs(combDps - singleEst) / combDps < 0.18 then
      pobSingleHitOnly = true
    end
  end
  local isMultiHitInnate = (innate and innate.multiHit == true) or (archetype == "roaming_bouncing_proj") or (sealMax > 0) or (repeatCount > 1)

  local magazineSize = maxPos(
    o and o.MagazineSize,
    o and o.AmmoCount,
    sd.magazineSize,
    sd.ammoCount,
    isCrossbow and sumSkillModBase(sk, "CrossbowClipSize") or 0,
    isCrossbow and sumSkillModBase(sk, "AdditionalBoltsLoaded") or 0
  )
  local reloadTime = maxPos(o and o.ReloadTime, o and o.CrossbowReloadTime, sd.reloadTime)
  local reloadSpeedMod = isCrossbow and calcModMult(sk, "ReloadSpeed", o and o.ReloadSpeedMod) or 0
  local fuseTime = maxPos(o and o.FuseTime, sd.fuseTime, sd.detonationTime)

  local speedVal = safeNum(o and o.Speed)
  local attackCastTime = safeNum(o and o.Time)
  local stageRampSec = (stageMax > 1 and speedVal > 0) and (stageMax / speedVal) or 0
  local aftershockChance = maxPos(
    o and o.AftershockChance,
    sumSkillModBase(sk, "AftershockChance"),
    sumSkillModBase(sk, "SlamAftershockChance")
  )
  local exertedAttacks = maxPos(o and o.ExertedAttackCount, sumSkillModBase(sk, "ExertedAttacks"))

  local totemPlacementSpeed = safeNum(o and o.TotemPlacementSpeed)
  local totemPlacementTime = maxPos(
    o and o.TotemPlacementTime,
    (totemPlacementSpeed > 0) and (1 / totemPlacementSpeed) or 0
  )
  local totemLife = safeNum(o and o.TotemLife)

  local minionOut = (o and type(o.Minion) == "table") and o.Minion or nil
  local minionLife = safeNum(minionOut and minionOut.Life)
  local minionDps = maxPos(minionOut and minionOut.CombinedDPS, minionOut and minionOut.TotalDPS)
  local minionMsRaw = safeNum(minionOut and minionOut.EffectiveMovementSpeedMod)
  local minionMoveSpeed = minionMsRaw > 0 and ((minionMsRaw - 1) * 100) or 0
  local spiritCost = maxPos(sd.SpiritReservedBase, o and o.SpiritReservedBase)

  local metaEnergyGainMod = isTriggered and calcModMult(sk, "MetaEnergyGain", o and o.EnergyGainMod) or 0
  local maxEnergy = maxPos(o and o.MaxEnergy, o and o.EnergyRequired, sd.energyRequired)
  local poisonStacks = safeNum(o and o.PoisonStacks)
  local poisonStackCap = maxPos(o and o.PoisonStackLimit, sumSkillModBase(sk, "PoisonStackLimit"))
  local manaPerSecCost = safeNum(o and o.ManaPerSecondCost)

  return {
    archetype = archetype,
    isShapeshift = isShapeshift,
    pobSingleHitOnly = pobSingleHitOnly,
    isMultiHitInnate = isMultiHitInnate,
    skillPart = partIdx,
    skillPartName = partName,
    availableParts = availableParts,
    skillStageCount = stageCount,
    skillStageMax = stageMax,
    stageRampSec = stageRampSec,
    gemCount = gemCount,
    includeInFullDPS = includeInFullDPS,
    duration = duration,
    durationMod = durationMod,
    durationSecondary = durationSecondary,
    projSpeedMod = projSpeedMod,
    projCount = projCount,
    pierceCount = pierceCount,
    pierceChance = pierceChance,
    pierceAll = pierceAll,
    chainCount = chainCount,
    terrainChainChance = terrainChainChance,
    bouncesOffTerrain = bouncesOffTerrain,
    bounceCount = bounceCount,
    forkCount = forkCount,
    splitCount = splitCount,
    hitIntervalSec = hitIntervalSec,
    aoeRadius = aoeRadius,
    aoeMod = aoeMod,
    sealMax = sealMax,
    sealCooldown = sealCooldown,
    timeMaxSeals = timeMaxSeals,
    cooldown = cooldown,
    cooldownRecovery = cooldownRecovery,
    triggerRate = triggerRate,
    repeatCount = repeatCount,
    attackCastTime = attackCastTime,
    magazineSize = magazineSize,
    reloadTime = reloadTime,
    reloadSpeedMod = reloadSpeedMod,
    fuseTime = fuseTime,
    aftershockChance = aftershockChance,
    exertedAttacks = exertedAttacks,
    totemPlacementTime = totemPlacementTime,
    totemLife = totemLife,
    minionLife = minionLife,
    minionDps = minionDps,
    minionMoveSpeed = minionMoveSpeed,
    spiritCost = spiritCost,
    metaEnergyGainMod = metaEnergyGainMod,
    maxEnergy = maxEnergy,
    dotSharePct = dotSharePct,
    poisonStacks = poisonStacks,
    poisonStackCap = poisonStackCap,
    manaPerSecCost = manaPerSecCost,
  }
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
  local full = safeNum(o.FullDPS)
  if full > 0 then
    return full, "FullDPS"
  end
  -- 图腾/延迟引爆法术在 PoB2 中 CombinedDPS 可能退化为单发均伤 (CombinedAvg)，
  -- 而 TotalDPS 才是乘上施法频率后的真实 DPS；因此优先取两者较大值。
  local comb = safeNum(o.CombinedDPS)
  local tot = safeNum(o.TotalDPS)
  if comb > 0 or tot > 0 then
    if tot > comb * 1.001 then
      return tot, "TotalDPS"
    end
    return comb, "CombinedDPS"
  end
  for _, k in ipairs(DPS_PRIORITY) do
    local v = safeNum(o[k])
    if v > 0 then
      return v, k
    end
  end
  return 0, (lockKey and lockKey ~= "" and lockKey ~= "-") and lockKey or "TotalDPS"
end

local function hasSet1Weapons()
  if not build or not build.itemsTab or not build.itemsTab.slots then return false end
  local w1 = build.itemsTab.slots["Weapon 1"]
  local w2 = build.itemsTab.slots["Weapon 2"]
  return (w1 and w1.selItemId and w1.selItemId > 0)
      or (w2 and w2.selItemId and w2.selItemId > 0)
      or false
end

local function hasSet2Weapons()
  if not build or not build.itemsTab or not build.itemsTab.slots then return false end
  local s1 = build.itemsTab.slots["Weapon 1 Swap"]
  local s2 = build.itemsTab.slots["Weapon 2 Swap"]
  return (s1 and s1.selItemId and s1.selItemId > 0)
      or (s2 and s2.selItemId and s2.selItemId > 0)
      or false
end

local function hasDualWeapons()
  return hasSet1Weapons() and hasSet2Weapons()
end

local function calcWeaponSetSummary(ws)
  if not build or not build.calcsTab or not build.calcsTab.calcs then
    return nil
  end
  local ok, env = pcall(build.calcsTab.calcs.initEnv, build, "MAIN", { weaponSet = ws })
  if not ok or not env then return nil end
  local ok2 = pcall(build.calcsTab.calcs.perform, env)
  if not ok2 or not env.player or not env.player.output then return nil end
  local o = env.player.output
  local dps, dpsSource = pickDps(o, nil)
  local ehpInf = isInf(o.TotalEHP) and 1 or 0
  local rawEhp = safeNum(o.TotalEHP)
  local life = safeNum(o.Life)
  local es = safeNum(o.EnergyShield)
  local ehp = (ehpInf == 0 and rawEhp > 0) and rawEhp or (life + es)
  local msRaw = safeNum(o.EffectiveMovementSpeedMod)
  local moveSpeed = msRaw > 0 and ((msRaw - 1) * 100) or 0
  local skillMsRaw = safeNum(o.MovementSpeedWhileUsingSkill)
  local skillMoveSpeed = skillMsRaw > 0 and ((skillMsRaw - 1) * 100) or moveSpeed
  local asRaw = safeNum(o.ActionSpeedMod)
  local actionSpeed = asRaw > 0 and ((asRaw - 1) * 100) or 0
  local eleMaxHit = minPos(o.FireMaximumHitTaken, o.ColdMaximumHitTaken, o.LightningMaximumHitTaken)
  return {
    dps = dps,
    dpsSource = dpsSource,
    ehp = ehp,
    physMaxHit = safeNum(o.PhysicalMaximumHitTaken),
    eleMaxHit = eleMaxHit,
    chaosMaxHit = safeNum(o.ChaosMaximumHitTaken),
    mana = safeNum(o.Mana),
    es = es,
    lifeUnreserved = safeNum(o.LifeUnreserved),
    spirit = safeNum(o.Spirit),
    spiritUnreserved = safeNum(o.SpiritUnreserved),
    speed = safeNum(o.Speed),
    moveSpeed = moveSpeed,
    skillMoveSpeed = skillMoveSpeed,
    actionSpeed = actionSpeed,
  }
end

local function isTravelSkillObj(sk, name)
  if sk and sk.skillTypes and SkillType then
    if (SkillType.Travel and sk.skillTypes[SkillType.Travel])
      or (SkillType.Movement and sk.skillTypes[SkillType.Movement])
      or (SkillType.Blink and sk.skillTypes[SkillType.Blink]) then
      return true
    end
  end
  local n = tostring(name or ""):lower()
  if n == "" then return false end
  if n:find("blink", 1, true)
    or n:find("whirling slash", 1, true)
    or n:find("tempest flurry", 1, true)
    or n:find("shield charge", 1, true)
    or n:find("leap slam", 1, true)
    or n:find("lightning warp", 1, true)
    or n:find("vaulting impact", 1, true)
    or n:find("dodge roll", 1, true)
    or n:find("dash", 1, true)
    or n:find("stampede", 1, true)
    or n:find("flicker strike", 1, true)
    or n:find("rhoa", 1, true)
    or n:find("disengage", 1, true)
    or n:find("escape shot", 1, true)
    or n:find("thunderous leap", 1, true)
    or n:find("pounce", 1, true) then
    return true
  end
  return false
end

local function collectAllSkillsDps()
  local list = {}
  if not build or not build.skillsTab or not build.skillsTab.socketGroupList then
    return list
  end
  local prev = build.mainSocketGroup
  local seenItemOnly = {}
  local dualWs = hasDualWeapons()
  for idx, sg in ipairs(build.skillsTab.socketGroupList) do
    if sg and sg.enabled ~= false then
      local isSingleSource = sg.source and sg.source ~= "" and sg.gemList and #sg.gemList <= 1
      local skip = false
      if isSingleSource and idx ~= prev then
        local nm = sg.gemList[1] and sg.gemList[1].nameSpec or ""
        if nm ~= "" and seenItemOnly[nm] then
          skip = true
        end
      end
      if not skip then
        build.mainSocketGroup = idx
        local ok1, senv = pcall(build.calcsTab.calcs.initEnv, build, "MAIN")
        if ok1 and senv then
          local ok2 = pcall(build.calcsTab.calcs.perform, senv)
          if ok2 and senv.player and senv.player.output then
            local so = senv.player.output
            local sk = senv.player.mainSkill
            local ge = sk and sk.activeEffect and sk.activeEffect.grantedEffect
            local name = (ge and ge.name) or (sg.displayLabel or ("Group " .. idx))
            local lv = safeNum(sk and sk.activeEffect and sk.activeEffect.level)
            local dps, dpsSource = pickDps(so, nil)
            local avgHit = safeNum(so.AverageHit)
            if avgHit <= 0 then
              avgHit = safeNum(so.AverageDamage)
            end
            local isTravel = isTravelSkillObj(sk, name)
            local curWs = (senv and senv.weaponSet) or 1
            local curSpeed = safeNum(so.Speed)
            local curCooldown = safeNum(so.Cooldown)
            local curCastTime = safeNum(so.Time)
            local msRaw = safeNum(so.EffectiveMovementSpeedMod)
            local skMs = msRaw > 0 and ((msRaw - 1) * 100) or 0
            local skUseMsRaw = safeNum(so.MovementSpeedWhileUsingSkill)
            local skUseMs = skUseMsRaw > 0 and ((skUseMsRaw - 1) * 100) or skMs

            local speedWs1 = 0
            local speedWs2 = 0
            local cooldownWs1 = 0
            local cooldownWs2 = 0
            if dualWs and (curSpeed > 0 or isTravel or idx == prev) then
              if curWs == 1 then
                speedWs1 = curSpeed
                cooldownWs1 = curCooldown
                local okAlt, altEnv = pcall(build.calcsTab.calcs.initEnv, build, "MAIN", { weaponSet = 2 })
                if okAlt and altEnv and pcall(build.calcsTab.calcs.perform, altEnv) and altEnv.player and altEnv.player.output then
                  speedWs2 = safeNum(altEnv.player.output.Speed)
                  cooldownWs2 = safeNum(altEnv.player.output.Cooldown)
                end
              else
                speedWs2 = curSpeed
                cooldownWs2 = curCooldown
                local okAlt, altEnv = pcall(build.calcsTab.calcs.initEnv, build, "MAIN", { weaponSet = 1 })
                if okAlt and altEnv and pcall(build.calcsTab.calcs.perform, altEnv) and altEnv.player and altEnv.player.output then
                  speedWs1 = safeNum(altEnv.player.output.Speed)
                  cooldownWs1 = safeNum(altEnv.player.output.Cooldown)
                end
              end
            end
            local mech = extractSkillMechanics(sk, so, sg)
            local isUtilityRelevant = (safeNum(curCooldown) > 0 or safeNum(mech.cooldown) > 0 or safeNum(mech.sealMax) > 0 or safeNum(mech.duration) > 0 or safeNum(mech.triggerRate) > 0)
            if dps > 0 or avgHit > 0 or isTravel or isUtilityRelevant or idx == prev then
              seenItemOnly[name] = true
              table.insert(list, {
                index = idx,
                isMain = (idx == prev),
                isTravel = isTravel,
                weaponSet = curWs,
                name = name,
                level = lv,
                dps = dps,
                dpsSource = dpsSource,
                avgHit = avgHit,
                avgBurstDamage = safeNum(so.AverageBurstDamage),
                speed = curSpeed,
                speedWs1 = speedWs1,
                speedWs2 = speedWs2,
                castTime = curCastTime,
                cooldown = curCooldown,
                cooldownWs1 = cooldownWs1,
                cooldownWs2 = cooldownWs2,
                cooldownRecovery = mech.cooldownRecovery,
                triggerRate = mech.triggerRate,
                moveSpeed = skMs,
                skillMoveSpeed = skUseMs,
                duration = mech.duration,
                durationMod = mech.durationMod,
                durationSecondary = mech.durationSecondary,
                projSpeedMod = mech.projSpeedMod,
                projCount = mech.projCount,
                pierceCount = mech.pierceCount,
                pierceAll = mech.pierceAll,
                chainCount = mech.chainCount,
                terrainChainChance = mech.terrainChainChance,
                bouncesOffTerrain = mech.bouncesOffTerrain,
                bounceCount = mech.bounceCount,
                forkCount = mech.forkCount,
                splitCount = mech.splitCount,
                hitIntervalSec = mech.hitIntervalSec,
                aoeRadius = mech.aoeRadius,
                aoeMod = mech.aoeMod,
                skillPart = mech.skillPart,
                skillPartName = mech.skillPartName,
                availableParts = mech.availableParts,
                skillStageCount = mech.skillStageCount,
                skillStageMax = mech.skillStageMax,
                stageRampSec = mech.stageRampSec,
                archetype = mech.archetype,
                isShapeshift = mech.isShapeshift,
                pobSingleHitOnly = mech.pobSingleHitOnly,
                isMultiHitInnate = mech.isMultiHitInnate,
                gemCount = mech.gemCount,
                includeInFullDPS = mech.includeInFullDPS,
                sealMax = mech.sealMax,
                sealCooldown = mech.sealCooldown,
                timeMaxSeals = mech.timeMaxSeals,
                repeatCount = mech.repeatCount,
                magazineSize = mech.magazineSize,
                reloadTime = mech.reloadTime,
                reloadSpeedMod = mech.reloadSpeedMod,
                fuseTime = mech.fuseTime,
                aftershockChance = mech.aftershockChance,
                exertedAttacks = mech.exertedAttacks,
                totemPlacementTime = mech.totemPlacementTime,
                totemLife = mech.totemLife,
                minionLife = mech.minionLife,
                minionDps = mech.minionDps,
                minionMoveSpeed = mech.minionMoveSpeed,
                spiritCost = mech.spiritCost,
                metaEnergyGainMod = mech.metaEnergyGainMod,
                maxEnergy = mech.maxEnergy,
                dotSharePct = mech.dotSharePct,
                poisonStacks = mech.poisonStacks,
                poisonStackCap = mech.poisonStackCap,
                manaPerSecCost = mech.manaPerSecCost,
                critChance = safeNum(so.CritChance),
                preCritChance = safeNum(so.PreEffectiveCritChance),
                critMult = safeNum(so.CritMultiplier) * 100,
                manaCost = safeNum(so.ManaCost),
                lifeCost = safeNum(so.LifeCost),
                totemLimit = safeNum(so.ActiveTotemLimit),
                minionLimit = safeNum(so.ActiveMinionLimit),
              })
            end
          end
        end
      end
    end
  end
  build.mainSocketGroup = prev
  return list
end

local function collectUnparsedMods()
  local list = {}
  if not build or not build.itemsTab or not build.itemsTab.items then
    return list
  end
  local itemSlot = {}
  if build.itemsTab.slots then
    for sname, slot in pairs(build.itemsTab.slots) do
      if slot and slot.selItemId and slot.selItemId > 0 then
        itemSlot[slot.selItemId] = sname
      end
    end
  end
  if build.spec and build.spec.jewels then
    for nid, iid in pairs(build.spec.jewels) do
      if iid and iid > 0 and not itemSlot[iid] then
        itemSlot[iid] = "Tree Jewel (Node " .. tostring(nid) .. ")"
      end
    end
  end
  local seen = {}
  for id, slotName in pairs(itemSlot) do
    local item = build.itemsTab.items[id]
    if item then
      local iname = item.name or item.baseName or ("Item " .. tostring(id))
      for _, listName in ipairs({"runeModLines", "enchantModLines", "implicitModLines", "explicitModLines"}) do
        local lst = item[listName]
        if lst then
          for _, ml in ipairs(lst) do
            if ml and ml.line and ml.line ~= "" and (not ml.modList or #ml.modList == 0) then
              local key = slotName .. "|" .. ml.line
              if not seen[key] then
                seen[key] = true
                table.insert(list, {
                  slot = slotName,
                  itemName = iname,
                  line = ml.line,
                })
              end
            end
          end
        end
      end
    end
  end
  return list
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
  local hasS1 = hasSet1Weapons()
  local hasS2 = hasSet2Weapons()
  local dualWs = hasS1 and hasS2

  local dps, dpsSource = pickDps(o, lockKey)
  local life = safeNum(o.Life)
  local es = safeNum(o.EnergyShield)
  local ehpInf = isInf(o.TotalEHP) and 1 or 0
  local rawEhp = safeNum(o.TotalEHP)
  local ehp = (ehpInf == 0 and rawEhp > 0) and rawEhp or (life + es)
  local chaosInf = (isInf(o.ChaosMaximumHitTaken) or o.ChaosInoculation) and 1 or 0
  local mainSk = env and env.player and env.player.mainSkill
  local mainSkLv = safeNum(mainSk and mainSk.activeEffect and mainSk.activeEffect.level)
  local mainSkName = (mainSk and mainSk.activeEffect and mainSk.activeEffect.grantedEffect and mainSk.activeEffect.grantedEffect.name) or ""
  local mainSg = build.skillsTab and build.skillsTab.socketGroupList and build.skillsTab.socketGroupList[build.mainSocketGroup]
  local mainMech = extractSkillMechanics(mainSk, o, mainSg)
  local activeTotems = safeNum(o.ActiveTotemLimit)
  local activeMinions = safeNum(o.ActiveMinionLimit)
  local lifeUnres = safeNum(o.LifeUnreserved)
  local lifeUnresPct = safeNum(o.LifeUnreservedPercent)

  local enemyIsBoss = ""
  if env and env.configInput and env.configInput.enemyIsBoss then
    enemyIsBoss = tostring(env.configInput.enemyIsBoss)
  elseif build.configTab and build.configTab.input and build.configTab.input.enemyIsBoss then
    enemyIsBoss = tostring(build.configTab.input.enemyIsBoss)
  end
  local enemyLevel = safeNum(env and env.enemyLevel)

  local critMultRaw = safeNum(o.CritMultiplier)
  local critMult = critMultRaw > 0 and (critMultRaw * 100) or 0
  local msRaw = safeNum(o.EffectiveMovementSpeedMod)
  local moveSpeed = msRaw > 0 and ((msRaw - 1) * 100) or 0
  local skillMsRaw = safeNum(o.MovementSpeedWhileUsingSkill)
  local skillMoveSpeed = skillMsRaw > 0 and ((skillMsRaw - 1) * 100) or moveSpeed
  local asRaw = safeNum(o.ActionSpeedMod)
  local actionSpeed = asRaw > 0 and ((asRaw - 1) * 100) or 0
  local avgHit = safeNum(o.AverageHit)
  if avgHit <= 0 then
    avgHit = safeNum(o.AverageDamage)
  end
  local evadeChance = maxPos(o.EvadeChance, o.MeleeEvadeChance, o.ProjectileEvadeChance)
  local eleMaxHit = minPos(o.FireMaximumHitTaken, o.ColdMaximumHitTaken, o.LightningMaximumHitTaken)

  local spirit = safeNum(o.Spirit)
  local spiritUnres = safeNum(o.SpiritUnreserved)
  local spiritRes = math.max(0, spirit - spiritUnres)
  local sp1, u1 = spirit, spiritUnres
  local sp2, u2 = 0, 0
  local wsSummary1, wsSummary2 = nil, nil

  local activeWsSummary = {
    dps = dps,
    dpsSource = dpsSource,
    ehp = ehp,
    physMaxHit = safeNum(o.PhysicalMaximumHitTaken),
    eleMaxHit = eleMaxHit,
    chaosMaxHit = safeNum(o.ChaosMaximumHitTaken),
    mana = safeNum(o.Mana),
    es = es,
    lifeUnreserved = lifeUnres,
    spirit = spirit,
    spiritUnreserved = spiritUnres,
    speed = safeNum(o.Speed),
    moveSpeed = moveSpeed,
    skillMoveSpeed = skillMoveSpeed,
    actionSpeed = actionSpeed,
  }

  if dualWs or includeAudit then
    if activeWs == 1 then
      wsSummary1 = activeWsSummary
      wsSummary2 = calcWeaponSetSummary(2)
      sp1, u1 = spirit, spiritUnres
      if wsSummary2 then sp2, u2 = wsSummary2.spirit, wsSummary2.spiritUnreserved end
    else
      wsSummary2 = activeWsSummary
      wsSummary1 = calcWeaponSetSummary(1)
      sp2, u2 = spirit, spiritUnres
      if wsSummary1 then sp1, u1 = wsSummary1.spirit, wsSummary1.spiritUnreserved end
    end
  end

  local auditList = includeAudit and collectSpiritAudit() or {}
  local allSkills = includeAudit and collectAllSkillsDps() or {}
  local unparsed = includeAudit and collectUnparsedMods() or {}

  return {
    metricsVersion = 5,
    dps = dps,
    dpsSource = dpsSource,
    mainSkillName = mainSkName,
    mainSkillLevel = mainSkLv,
    skillArchetype = mainMech.archetype,
    isShapeshift = mainMech.isShapeshift and 1 or 0,
    pobSingleHitOnly = mainMech.pobSingleHitOnly and 1 or 0,
    isMultiHitInnate = mainMech.isMultiHitInnate and 1 or 0,
    activeTotemLimit = activeTotems,
    activeMinionLimit = activeMinions,
    skillPart = mainMech.skillPart,
    skillPartName = mainMech.skillPartName,
    skillStageCount = mainMech.skillStageCount,
    skillStageMax = mainMech.skillStageMax,
    stageRampSec = mainMech.stageRampSec,
    gemCount = mainMech.gemCount,
    includeInFullDPS = mainMech.includeInFullDPS and 1 or 0,
    duration = mainMech.duration,
    durationMod = mainMech.durationMod,
    durationSecondary = mainMech.durationSecondary,
    projSpeedMod = mainMech.projSpeedMod,
    projectileCount = mainMech.projCount,
    pierceCount = mainMech.pierceCount,
    pierceChance = mainMech.pierceChance,
    pierceAll = mainMech.pierceAll and 1 or 0,
    chainCount = mainMech.chainCount,
    terrainChainChance = mainMech.terrainChainChance,
    bouncesOffTerrain = mainMech.bouncesOffTerrain and 1 or 0,
    bounceCount = mainMech.bounceCount,
    forkCount = mainMech.forkCount,
    splitCount = mainMech.splitCount,
    hitIntervalSec = mainMech.hitIntervalSec,
    aoeRadius = mainMech.aoeRadius,
    aoeMod = mainMech.aoeMod,
    presenceRadius = safeNum(o.PresenceRadiusMetres),
    sealMax = mainMech.sealMax,
    sealCooldown = mainMech.sealCooldown,
    timeMaxSeals = mainMech.timeMaxSeals,
    cooldownRecovery = mainMech.cooldownRecovery,
    triggerRate = mainMech.triggerRate,
    repeatCount = mainMech.repeatCount,
    magazineSize = mainMech.magazineSize,
    reloadTime = mainMech.reloadTime,
    reloadSpeedMod = mainMech.reloadSpeedMod,
    fuseTime = mainMech.fuseTime,
    aftershockChance = mainMech.aftershockChance,
    exertedAttacks = mainMech.exertedAttacks,
    totemPlacementTime = mainMech.totemPlacementTime,
    totemLife = mainMech.totemLife,
    minionLife = mainMech.minionLife,
    minionDps = mainMech.minionDps,
    minionMoveSpeed = mainMech.minionMoveSpeed,
    spiritCost = mainMech.spiritCost,
    metaEnergyGainMod = mainMech.metaEnergyGainMod,
    maxEnergy = mainMech.maxEnergy,
    dotSharePct = mainMech.dotSharePct,
    poisonStacks = mainMech.poisonStacks,
    poisonStackCap = mainMech.poisonStackCap,
    ehp = ehp,
    ehpInfinite = ehpInf,
    life = life,
    lifeUnreserved = lifeUnres,
    lifeUnreservedPct = lifeUnresPct,
    es = es,
    ward = safeNum(o.Ward),
    deflectChance = safeNum(o.DeflectChance),
    deflectionRating = safeNum(o.DeflectionRating),
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
    hasSet1Weapons = hasS1 and 1 or 0,
    hasSet2Weapons = hasS2 and 1 or 0,
    chaosInoculation = o.ChaosInoculation and 1 or 0,
    chaosMaxHitInfinite = chaosInf,
    armour = safeNum(o.Armour),
    physDR = safeNum(o.PhysicalDamageReduction),
    evasion = safeNum(o.Evasion),
    evadeChance = evadeChance,
    blockChance = safeNum(o.EffectiveBlockChance),
    spellBlockChance = safeNum(o.EffectiveSpellBlockChance),
    speed = safeNum(o.Speed),
    avgHit = avgHit,
    avgBurstDamage = safeNum(o.AverageBurstDamage),
    castTime = safeNum(o.Time),
    cooldown = safeNum(o.Cooldown),
    fullDps = safeNum(o.FullDPS),
    combinedDps = safeNum(o.CombinedDPS),
    totalDps = safeNum(o.TotalDPS),
    dotDps = safeNum(o.TotalDotDPS),
    igniteDps = safeNum(o.IgniteDPS),
    bleedDps = safeNum(o.BleedDPS),
    poisonDps = safeNum(o.PoisonDPS),
    cullingDps = safeNum(o.CullingDPS),
    shockEffect = safeNum(o.ShockEffect),
    chillEffect = safeNum(o.ChillEffect),
    freezeChance = safeNum(o.FreezeChance),
    critChance = safeNum(o.CritChance),
    preEffectiveCritChance = safeNum(o.PreEffectiveCritChance),
    critMult = critMult,
    critBifurcates = safeNum(o.CritBifurcates),
    hitChance = safeNum(o.HitChance),
    moveSpeed = moveSpeed,
    skillMoveSpeed = skillMoveSpeed,
    actionSpeed = actionSpeed,
    manaCost = safeNum(o.ManaCost),
    manaPerSecCost = safeNum(o.ManaPerSecondCost),
    manaUnreserved = safeNum(o.ManaUnreserved),
    manaRegen = safeNum(o.ManaRegenRecovery),
    manaLeech = safeNum(o.ManaLeechGainRate),
    lifeCost = safeNum(o.LifeCost),
    lifePerSecCost = safeNum(o.LifePerSecondCost),
    esCost = safeNum(o.ESCost),
    esPerSecCost = safeNum(o.ESPerSecondCost),
    lifeRegen = safeNum(o.LifeRegenRecovery),
    lifeLeech = safeNum(o.LifeLeechGainRate),
    lifeOnHit = safeNum(o.LifeOnHitRate),
    lifeRecovery = safeNum(o.LifeRegenRecovery) + safeNum(o.LifeLeechGainRate),
    esRegen = safeNum(o.EnergyShieldRegenRecovery),
    esLeech = safeNum(o.EnergyShieldLeechGainRate),
    esRecharge = safeNum(o.EnergyShieldRecharge),
    esRechargeDelay = safeNum(o.EnergyShieldRechargeDelay),
    esRecovery = safeNum(o.EnergyShieldRegenRecovery) + safeNum(o.EnergyShieldLeechGainRate),
    stunThreshold = safeNum(o.StunThreshold),
    ailmentThreshold = safeNum(o.AilmentThreshold),
    rage = safeNum(o.Rage),
    rageRegen = safeNum(o.RageRegenRecovery),
    powerCharges = safeNum(o.PowerCharges),
    powerChargesMax = safeNum(o.PowerChargesMax),
    frenzyCharges = safeNum(o.FrenzyCharges),
    frenzyChargesMax = safeNum(o.FrenzyChargesMax),
    enduranceCharges = safeNum(o.EnduranceCharges),
    enduranceChargesMax = safeNum(o.EnduranceChargesMax),
    fireRes = safeNum(o.FireResist),
    coldRes = safeNum(o.ColdResist),
    lightningRes = safeNum(o.LightningResist),
    chaosRes = o.ChaosInoculation and 100 or safeNum(o.ChaosResist),
    fireResMax = safeNum(o.FireResistMax),
    coldResMax = safeNum(o.ColdResistMax),
    lightningResMax = safeNum(o.LightningResistMax),
    chaosResMax = safeNum(o.ChaosResistMax),
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
    fireMaxHit = safeNum(o.FireMaximumHitTaken),
    coldMaxHit = safeNum(o.ColdMaximumHitTaken),
    lightningMaxHit = safeNum(o.LightningMaximumHitTaken),
    eleMaxHit = eleMaxHit,
    chaosMaxHit = safeNum(o.ChaosMaximumHitTaken),
    enemyIsBoss = enemyIsBoss,
    enemyLevel = enemyLevel,
    elapsedMs = elapsedMs,
  }, {
    mainSkillParts = mainMech.availableParts,
    spiritAudit = auditList,
    allSkillsDps = allSkills,
    unparsedMods = unparsed,
    ws1 = wsSummary1,
    ws2 = wsSummary2,
  }
end

local function encodeResultJson(tbl, extras)
  local out = {}
  for k, v in pairs(tbl) do
    out[k] = v
  end
  if extras then
    if extras.mainSkillParts and #extras.mainSkillParts > 0 then
      out.mainSkillParts = extras.mainSkillParts
    end
    if extras.spiritAudit and #extras.spiritAudit > 0 then
      out.spiritAudit = extras.spiritAudit
    end
    if extras.allSkillsDps and #extras.allSkillsDps > 0 then
      out.allSkillsDps = extras.allSkillsDps
    end
    if extras.unparsedMods and #extras.unparsedMods > 0 then
      out.unparsedMods = extras.unparsedMods
    end
    if extras.ws1 or extras.ws2 then
      out.weaponSetStats = { ws1 = extras.ws1, ws2 = extras.ws2 }
    end
  end
  return encodeJsonValue(out)
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
    mainCtrl:SetMode("BUILD", false, "Sim", xml, true)
    runCallback("OnFrame")
    runCallback("OnFrame")
    if mainCtrl.modes and mainCtrl.modes["BUILD"] then
      build = mainCtrl.modes["BUILD"]
    end
  end)
  local dt = nowMs() - t0
  if not ok then
    io.stdout:write("ERR\t" .. id .. "\t" .. tostring(err):gsub("[\r\n]+", " ") .. "\n")
    io.stdout:flush()
    return
  end
  local okOut, encoded = pcall(function()
    local res, audit = extractOutput(lockKey, includeAudit, dt)
    return encodeResultJson(res, audit)
  end)
  if not okOut then
    io.stdout:write("ERR\t" .. id .. "\t" .. tostring(encoded):gsub("[\r\n]+", " ") .. "\n")
    io.stdout:flush()
    return
  end
  io.stdout:write("RES_JSON\t" .. id .. "\t" .. encoded .. "\n")
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

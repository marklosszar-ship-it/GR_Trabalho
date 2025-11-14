import logging
from pysnmp.carrier.asyncore.dgram import udp
from pysnmp.entity import engine, config
from pysnmp.entity.rfc3413 import cmdrsp, context
from pysnmp.carrier.asyncore.dgram import udp
from pysnmp.hlapi.asyncio import *


# Logging
formatting = '[%(asctime)s-%(levelname)s]-(%(module)s) %(message)s'
logging.basicConfig(level=logging.DEBUG, format=formatting)
logging.info("Starting SNMP agent...")

# Create SNMP engine
snmpEngine = engine.SnmpEngine()

# UDP over IPv4 transport
config.addTransport(
    snmpEngine,
    udp.domainName,
    udp.UdpTransport().openServerMode(('0.0.0.0', 12345))
)

# SNMPv2c setup
config.addV1System(snmpEngine, 'my-area', 'public')

# VACM setup: allow access to entire TRAFFIC_MIB subtree
config.addVacmUser(snmpEngine,
                   2,              # SNMPv2c
                   'my-area',      # Security name
                   'noAuthNoPriv', # No auth, no privacy
                   (1, 3, 6, 1, 4, 1, 5000),  # TRAFFIC_MIB subtree
                   (1, 3, 6, 1, 4, 1, 5000))

# SNMP context
snmpContext = context.SnmpContext(snmpEngine)
mibBuilder = snmpContext.getMibInstrum().getMibBuilder()

# Load your MIB
logging.debug("Loading TRAFFIC_MIB...")
mibBuilder.loadModules('TRAFFIC_MIB')
logging.debug("TRAFFIC_MIB loaded")

# Import table and columns from your MIB
(roadEntry,
 roadId,
 trafficRate,
 vehicleCount,
 trafficLightColor,
 remainingTime,
 maxCapacity,
 outflowRate,
 greenLightTime,
 redLightTime) = mibBuilder.importSymbols(
    'TRAFFIC_MIB',
    'roadEntry',
    'roadId',
    'trafficRate',
    'vehicleCount',
    'trafficLightColor',
    'remainingTime',
    'maxCapacity',
    'outflowRate',
    'greenLightTime',
    'redLightTime'
)

# Example: insert a row in the road table
mibInstrum = snmpContext.getMibInstrum()
rowInstanceId = roadEntry.getInstIdFromIndices(1)  # Index = roadId = 1
mibInstrum.writeVars([
    (roadId.name + rowInstanceId, 1),
    (trafficRate.name + rowInstanceId, 50),
    (vehicleCount.name + rowInstanceId, 20),
    (trafficLightColor.name + rowInstanceId, 'green'),
    (remainingTime.name + rowInstanceId, 30),
    (maxCapacity.name + rowInstanceId, 200),
    (outflowRate.name + rowInstanceId, 40),
    (greenLightTime.name + rowInstanceId, 60),
    (redLightTime.name + rowInstanceId, 45),
])

logging.debug("Sample road row added")

# Register SNMP Applications
cmdrsp.GetCommandResponder(snmpEngine, snmpContext)
cmdrsp.SetCommandResponder(snmpEngine, snmpContext)
cmdrsp.NextCommandResponder(snmpEngine, snmpContext)
cmdrsp.BulkCommandResponder(snmpEngine, snmpContext)

# Keep dispatcher running
snmpEngine.transportDispatcher.jobStarted(1)
try:
    snmpEngine.transportDispatcher.runDispatcher()
except:
    snmpEngine.transportDispatcher.closeDispatcher()
    raise

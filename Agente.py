import asyncio
from pysnmp.entity import engine, config
from pysnmp.carrier.asyncio.dgram import udp
from pysnmp.entity.rfc3413 import cmdrsp, context
from pysnmp.smi import builder, instrum
from pysnmp.proto.rfc1902 import Integer32, OctetString, ObjectName

# -------------------------------
# Configurações do Agente
# -------------------------------
UDP_PORT = 1161
COMMUNITY = 'public'
MIB_NAME = 'TRAFFIC_MIB'
MIB_PY_DIR = './mibs/py'

# -------------------------------
# Dados de exemplo
# -------------------------------
ROAD_DATA = [
    {'roadId': 1, 'trafficRate': 50, 'vehicleCount': 200, 'trafficLightColor': 'green',
     'remainingTime': 30, 'maxCapacity': 500, 'outflowRate': 45, 'greenLightTime': 60, 'redLightTime': 60},
    {'roadId': 2, 'trafficRate': 70, 'vehicleCount': 350, 'trafficLightColor': 'red',
     'remainingTime': 45, 'maxCapacity': 600, 'outflowRate': 55, 'greenLightTime': 50, 'redLightTime': 70},
]

CONFIG_DATA = {
    'mapName': 'CityCenter',
    'numberOfRoads': 2,
    'simulationStep': 5,
    'currentSimulationTime': 100,
    'systemState': 'active',
    'fixedYellowTime': 5
}

STATES_DATA = {
    'mostCongestedRoad': 2,
    'totalVehicles': 550,
    'averageWaitTime': 25
}

DEST_DATA = [
    {'destinationRoadId': 1, 'destinationId': 101},
    {'destinationRoadId': 2, 'destinationId': 102}
]

# -------------------------------
# Função principal do agente
# -------------------------------
async def run_agent():
    snmp_engine = engine.SnmpEngine()
    mib_builder = snmp_engine.get_mib_builder()
    mib_builder.add_mib_sources(builder.DirMibSource(MIB_PY_DIR))
    mib_instrum = instrum.MibInstrumController(mib_builder)

    # Carregar a MIB
    try:
        mib_builder.load_modules(MIB_NAME)
        print(f"✅ MIB {MIB_NAME} carregada com sucesso!")
    except Exception as e:
        print(f"❌ Erro ao carregar MIB: {e}")
        return

    # Transporte UDP
    config.add_transport(
        snmp_engine,
        udp.DOMAIN_NAME,
        udp.UdpTransport().open_server_mode(('localhost', UDP_PORT))
    )
    config.add_v1_system(snmp_engine, 'public-area', COMMUNITY)

    # -------------------------------
    # Popular roadTable (OIDs corrigidos)
    # -------------------------------
    for road in ROAD_DATA:
        try:
            # Estrutura: enterprises.5000.1.1.1.{roadEntry}.{campo}.{roadId}
            base = f'1.3.6.1.4.1.5000.1.1.1'
            mib_instrum.write_variables([
                (ObjectName(f'{base}.2.{road["roadId"]}'), Integer32(road['trafficRate'])),
                (ObjectName(f'{base}.3.{road["roadId"]}'), Integer32(road['vehicleCount'])),
                (ObjectName(f'{base}.4.{road["roadId"]}'), OctetString(road['trafficLightColor'])),
                (ObjectName(f'{base}.5.{road["roadId"]}'), Integer32(road['remainingTime'])),
                (ObjectName(f'{base}.6.{road["roadId"]}'), Integer32(road['maxCapacity'])),
                (ObjectName(f'{base}.7.{road["roadId"]}'), Integer32(road['outflowRate'])),
                (ObjectName(f'{base}.8.{road["roadId"]}'), Integer32(road['greenLightTime'])),
                (ObjectName(f'{base}.9.{road["roadId"]}'), Integer32(road['redLightTime']))
            ])
        except Exception as e:
            print(f"❌ Erro ao popular road {road['roadId']}: {e}")

    # -------------------------------
    # Popular trafficConfig
    # -------------------------------
    for idx, (field, value) in enumerate(CONFIG_DATA.items(), start=1):
        try:
            val = Integer32(value) if isinstance(value, int) else OctetString(value)
            oid = ObjectName(f'1.3.6.1.4.1.5000.2.{idx}')
            mib_instrum.write_variables([(oid, val)])
        except Exception as e:
            print(f"❌ Erro ao popular {field}: {e}")

    # -------------------------------
    # Popular trafficStates
    # -------------------------------
    for idx, (field, value) in enumerate(STATES_DATA.items(), start=1):
        try:
            oid = ObjectName(f'1.3.6.1.4.1.5000.3.{idx}')
            mib_instrum.write_variables([(oid, Integer32(value))])
        except Exception as e:
            print(f"❌ Erro ao popular {field}: {e}")

    # -------------------------------
    # Popular destinationRoadTable
    # -------------------------------
    for dest in DEST_DATA:
        try:
            base = '1.3.6.1.4.1.5000.4.1'
            mib_instrum.write_variables([
                (ObjectName(f'{base}.1.{dest["destinationRoadId"]}'), Integer32(dest['destinationRoadId'])),
                (ObjectName(f'{base}.2.{dest["destinationRoadId"]}'), Integer32(dest['destinationId']))
            ])
        except Exception as e:
            print(f"❌ Erro ao popular destino {dest['destinationRoadId']}: {e}")

    # -------------------------------
    # Registrando command responders
    # -------------------------------
    snmp_context = context.SnmpContext(snmp_engine)
    cmdrsp.GetCommandResponder(snmp_engine, snmp_context)
    cmdrsp.NextCommandResponder(snmp_engine, snmp_context)
    cmdrsp.SetCommandResponder(snmp_engine, snmp_context)

    print(f"🚦 Agente SNMP ativo na porta {UDP_PORT}")
    await asyncio.Event().wait()


if _name_ == '_main_':
    asyncio.run(run_agent())
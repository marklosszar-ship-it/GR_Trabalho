import asyncio
from pysnmp.entity import engine, config
from pysnmp.carrier.asyncio.dgram import udp
from pysnmp.entity.rfc3413 import cmdrsp, context
from pysnmp.smi import builder, instrum
from pysnmp.proto.rfc1902 import OctetString

# -------------------------------
# Configurações
# -------------------------------
UDP_PORT = 1161
COMMUNITY = 'public'
SECURITY_NAME = 'my-area'  # nome usado para mapear a comunidade
MIB_NAME = 'TRAFFIC_MIB'
MIB_PY_DIR = './mibs/py'
MAP_NAME_VALUE = "CityCenter"

async def run_agent():
    # -------------------------------
    # Criar SNMP engine e MIB
    # -------------------------------
    snmp_engine = engine.SnmpEngine()
    mib_builder = snmp_engine.get_mib_builder()
    mib_builder.add_mib_sources(builder.DirMibSource(MIB_PY_DIR))
    mib_instrum = instrum.MibInstrumController(mib_builder)

    # -------------------------------
    # Carregar a MIB
    # -------------------------------
    try:
        mib_builder.load_modules(MIB_NAME)
        print(f"✅ MIB {MIB_NAME} carregada com sucesso!")
    except Exception as e:
        print(f"❌ Erro ao carregar MIB: {e}")
        return

    # -------------------------------
    # Criar instância scalar mapName
    # -------------------------------
    try:
        mapNameScalar = mib_builder.import_symbols(MIB_NAME, "mapName")[0]
        (MibScalarInstance,) = mib_builder.import_symbols("SNMPv2-SMI", "MibScalarInstance")

        mapNameInstance = MibScalarInstance(
            mapNameScalar.name,
            (0,),  # índice do scalar
            mapNameScalar.syntax.clone(MAP_NAME_VALUE)
        )
        mib_builder.export_symbols(MIB_NAME, mapNameInstance=mapNameInstance)
        print(f"✅ mapName instância criada com valor: {MAP_NAME_VALUE}")
        print(f"🔹 OID completo de mapName: {'.'.join(map(str, mapNameScalar.name + (0,)))}")
    except Exception as e:
        print(f"❌ Não foi possível popular mapName: {e}")
        return

    # -------------------------------
    # Configuração transporte SNMP
    # -------------------------------
    config.add_transport(
        snmp_engine,
        udp.DOMAIN_NAME,
        udp.UdpTransport().open_server_mode(('localhost', UDP_PORT))
    )

    # -------------------------------
    # Configuração SNMPv2c
    # -------------------------------
    config.add_v1_system(snmp_engine, SECURITY_NAME, COMMUNITY)

    # -------------------------------
    # Configuração VACM para mapName
    # -------------------------------

    #O problema possivelmente é neste bloco
    mapNameOID = mapNameScalar.name + (0,)  # incluir índice do scalar
    config.add_vacm_user(
        snmp_engine,
        2,  # SNMPv2c
        OctetString(SECURITY_NAME),
        'noAuthNoPriv',
        mapNameOID,
        mapNameOID
    )
    #Fim
    # -------------------------------
    # Contexto SNMP
    # -------------------------------
    snmp_context = context.SnmpContext(snmp_engine)

    # -------------------------------
    # Command responders
    # -------------------------------
    cmdrsp.GetCommandResponder(snmp_engine, snmp_context)
    cmdrsp.NextCommandResponder(snmp_engine, snmp_context)
    cmdrsp.SetCommandResponder(snmp_engine, snmp_context)

    print(f"🚦 Agente SNMP ativo na porta {UDP_PORT}")
    await asyncio.Event().wait()


if __name__ == '__main__':
    asyncio.run(run_agent())

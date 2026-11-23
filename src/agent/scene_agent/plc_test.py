"""PLCopen test harness and scan-acknowledged Modbus transport for isolated models.

The host writes staging requests, never the PLC's declared output channels.
The harness copies requests to outputs and inputs to separate observation slots.
"""
import re
import time
import xml.etree.ElementTree as ET

from .common import SceneError, digest, write_json

VERSION='308sjk.isolated_io_harness/1'


def harness(ir):
    from .device_ir import facts_digest
    points=ir['io'];used={20};names=set()
    for p in points:
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',p['name']):
            raise SceneError('PLC 测试通道名称不是有效标识符：'+p['name'],'plc_io')
        if p['name'].lower() in names:raise SceneError('PLC 通道名称忽略大小写后重复','plc_io')
        names.add(p['name'].lower())
        if p['type']=='INT':used.add(int(p['address'][3:]))
    free=iter(i for i in range(64,1024) if i not in used)
    def slot():
        try:return next(free)
        except StopIteration:raise SceneError('隔离 PLC 测试寄存器不足','plc_io')
    contract={'format':VERSION,'device_ir_sha256':facts_digest(ir),'io':[{k:p[k] for k in ('name','dir','type','address','initial')} for p in points],
        'identity':[slot() for _ in range(16)],'request':slot(),'commit':slot(),'ack':slot(),
        'staging':{p['name']:slot() for p in points if p['dir']=='output'},
        'observed':{p['name']:slot() for p in points if p['dir']=='input'}}
    contract['identity_sha256']=digest(contract)
    words=[int(contract['identity_sha256'][i:i+4],16) for i in range(0,64,4)]
    prefix='vcheck_'
    while any(p['name'].lower().startswith(prefix.lower()) for p in points):prefix='x_'+prefix
    ns='http://www.plcopen.org/xml/tc6_0201';ET.register_namespace('',ns)
    def node(parent,tag,**attrs):return ET.SubElement(parent,'{'+ns+'}'+tag,attrs)
    root=ET.Element('{'+ns+'}project')
    node(root,'fileHeader',companyName='308sjk',productName='Isolated IO harness',productVersion='1',creationDateTime='2026-11-03T00:00:00')
    node(root,'contentHeader',name='isolated_model_io')
    types=node(root,'types');node(types,'dataTypes');pous=node(types,'pous');pou=node(pous,'pou',name='PLC_PRG',pouType='program')
    local=node(node(pou,'interface'),'localVars')
    def variable(name,typ,address):
        v=node(local,'variable',name=name,address=address);node(node(v,'type'),typ)
    for p in points:variable(p['name'],p['type'],p['address'])
    variables={}
    for label in ('request','commit','ack'):
        variables[contract[label]]=prefix+label
    for i,address in enumerate(contract['identity']):variables[address]=prefix+'id'+str(i)
    for mapping in ('staging','observed'):
        for i,address in enumerate(contract[mapping].values()):variables[address]=prefix+mapping+str(i)
    for address,name in variables.items():variable(name,'WORD','%QW'+str(address))
    lines=[variables[address]+':= WORD#16#'+format(word,'04X')+';' for address,word in zip(contract['identity'],words)]
    lines+=['IF '+variables[contract['request']]+' = '+variables[contract['commit']]+' THEN']
    for p in points:
        if p['dir']=='output':
            value=variables[contract['staging'][p['name']]]
            value='('+value+' <> WORD#0)' if p['type']=='BOOL' else 'WORD_TO_INT('+value+')'
            lines.append('  '+p['name']+' := '+value+';')
        else:
            value='BOOL_TO_WORD('+p['name']+')' if p['type']=='BOOL' else 'INT_TO_WORD('+p['name']+')'
            lines.append('  '+variables[contract['observed'][p['name']]]+' := '+value+';')
    lines+=['  '+variables[contract['ack']]+' := '+variables[contract['request']]+';','END_IF;']
    st=node(node(pou,'body'),'ST');ET.SubElement(st,'{http://www.w3.org/1999/xhtml}p').text='\n'.join(lines)
    node(root,'instances')
    text=ET.tostring(root,encoding='unicode')
    from src.pipeline.xml2st import parse
    from io import StringIO
    # The regular converter remains the only source of executable ST.
    problems,_=parse(StringIO(text))
    if problems:raise SceneError('PLC 测试 XML 无效：'+str(problems),'plc_io')
    return contract,text


def save_harness(ir,folder):
    from pathlib import Path
    contract,xml=harness(ir);folder=Path(folder)
    write_json(folder/'plc_test_contract.json',contract)
    (folder/'io_test.xml').write_text(xml,encoding='utf-8')
    return contract


class PlcTestTransport:
    """One owned connection per test run. No production freeze bypass."""
    def __init__(self,ir,client,*,timeout=3.,cancelled=None):
        self.contract,_=harness(ir);self.client=client;self.points={p['name']:p for p in ir['io']}
        self.timeout=float(timeout);self.cancelled=cancelled or (lambda:None)
        self.outputs={p['name']:p['initial'] for p in ir['io'] if p['dir']=='output'}
        self.inputs={p['name']:p['initial'] for p in ir['io'] if p['dir']=='input'}
        self.trace=[];self.seq=0;self.verified=False
        from src.pipeline.modbus_io import SafeCoilIO
        self.coils=SafeCoilIO(client)
        self.verify()
        self.seq=self.read_word(self.contract['ack'])

    @staticmethod
    def coil(address):
        m=re.fullmatch(r'%QX(\d+)\.([0-7])',address)
        if not m:raise SceneError('无效的 PLC 位地址','plc_io')
        return int(m[1])*8+int(m[2])

    def read_word(self,address):
        try:r=self.client.read_holding_registers(address=address,count=1)
        except TypeError:r=self.client.read_holding_registers(address,1)
        if r is None or r.isError() or len(r.registers)!=1:raise SceneError('PLC 寄存器读取失败','plc_io')
        return int(r.registers[0])

    def write_word(self,address,value):
        r=self.client.write_register(address=address,value=int(value)&65535)
        if r is None or r.isError():raise SceneError('PLC 寄存器写入失败','plc_io')

    def verify(self):
        addresses=self.contract['identity'];start,end=min(addresses),max(addresses)
        try:r=self.client.read_holding_registers(address=start,count=end-start+1)
        except TypeError:r=self.client.read_holding_registers(start,end-start+1)
        if r is None or r.isError() or len(r.registers)!=end-start+1:raise SceneError('PLC 程序身份读取失败','plc_io')
        actual=''.join(format(int(r.registers[a-start]),'04x') for a in addresses)
        if actual!=self.contract['identity_sha256']:
            raise SceneError('OpenPLC 未运行当前设备的隔离 IO 测试程序；未写入测试请求','plc_io')
        self.verified=True

    def read_point(self,p):
        if p['type']=='BOOL':return bool(self.coils.read(self.coil(p['address'])))
        n=self.read_word(int(p['address'][3:]))
        return n-65536 if p.get('range',[-32768])[0]<0 and n>32767 else n

    def exchange(self,outputs=None,inputs=None):
        self.cancelled();self.verify()
        for updates,direction in ((outputs or {},'output'),(inputs or {},'input')):
            for name,value in updates.items():
                p=self.points.get(name)
                if not p or p['dir']!=direction:raise SceneError('PLC 测试通道方向错误','plc_io')
                if p['type']=='BOOL' and type(value) is not bool:raise SceneError('PLC BOOL 类型错误','plc_io')
                if p['type']=='INT' and (type(value) is not int or not p.get('range',[-32768,32767])[0]<=value<=p.get('range',[-32768,32767])[1]):
                    raise SceneError('PLC 测试数值越界','plc_io')
        self.outputs.update(outputs or {});self.inputs.update(inputs or {})
        self.seq=self.seq%65535+1
        # Break the guard first; commit last. A scan cannot acknowledge a partial request.
        self.write_word(self.contract['request'],self.seq)
        for name,value in self.outputs.items():self.write_word(self.contract['staging'][name],value)
        for name,value in self.inputs.items():
            p=self.points[name]
            if p['type']=='BOOL':self.coils.write(self.coil(p['address']),value)
            else:self.write_word(int(p['address'][3:]),value)
        self.write_word(self.contract['commit'],self.seq)
        deadline=time.monotonic()+self.timeout
        while self.read_word(self.contract['ack'])!=self.seq:
            self.cancelled()
            if time.monotonic()>deadline:raise SceneError('PLC 扫描确认超时','plc_io')
            time.sleep(.002)
        actual={name:self.read_point(self.points[name]) for name in self.outputs}
        observed={}
        for name,address in self.contract['observed'].items():
            p=self.points[name];value=self.read_word(address)
            observed[name]=bool(value) if p['type']=='BOOL' else value-65536 if p.get('range',[-32768])[0]<0 and value>32767 else value
        if self.read_word(self.contract['ack'])!=self.seq:raise SceneError('PLC 测试快照版本发生变化','plc_io')
        event={'sequence':self.seq,'requested_outputs':dict(self.outputs),'actual_outputs':actual,
               'published_inputs':dict(self.inputs),'plc_observed_inputs':observed}
        self.trace.append(event)
        if actual!=self.outputs:raise SceneError('PLC 输出与测试请求不符','io_binding',event)
        if observed!=self.inputs:raise SceneError('PLC 实际读取的输入与模型反馈不符','io_binding',event)
        return actual

    def close(self):self.client.close()


def connect(ir,config,cancelled=None):
    if not ir.get('simulation_requirements'):raise SceneError('AML 未声明响应超时，不能启动 PLC 校验','plc_io')
    if not isinstance(config,dict) or set(config)!={'host','port'} or not isinstance(config['host'],str) or not config['host'].strip():
        raise SceneError('请配置隔离 OpenPLC 测试 Runtime 的 host/port','plc_io')
    if type(config['port']) is not int or not 1<=config['port']<=65535:raise SceneError('PLC 端口无效','plc_io')
    from src.pipeline.modbus_io import connect as modbus_connect
    client=modbus_connect(config['host'],config['port'])
    try:return PlcTestTransport(ir,client,timeout=ir['simulation_requirements']['response_timeout_s'],cancelled=cancelled)
    except BaseException:
        client.close();raise

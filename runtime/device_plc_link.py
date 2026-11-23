"""Frozen-device Modbus adapter; consumes existing PLC programs, never generates/deploys them."""
import re
from pathlib import Path

from src.agent.scene_agent.common import SceneError, read_json, digest
from src.pipeline.modbus_io import SafeCoilIO


class DevicePlcLink:
    def __init__(self,runtime,client,freeze_manifest,prog_id):
        self.runtime,self.client,self.prog_id=runtime,client,prog_id
        if any(p['address']=='%QW20' for p in runtime.meta['io']):
            raise SceneError('AML IO conflicts with reserved program identity register %QW20','device_description')
        manifest=read_json(freeze_manifest)
        if runtime.meta['candidate'] or manifest.get('status')!='model_frozen':raise SceneError('Only frozen approved models can connect to PLC')
        if manifest.get('scene_sha256')!=runtime.meta['model_xml_sha256'] or manifest.get('device_ir_sha256')!=runtime.meta['device_ir_sha256']:
            raise SceneError('Freeze does not match current scene/DeviceIR')
        if manifest.get('checks',{}).get('definition_sha256')!=runtime.definition_sha256:
            raise SceneError('Freeze does not match behavior/IO configuration')
        from src.agent.scene_agent.rules import review_digest,implementation_hashes
        for r in runtime.meta['rules']:
            if not r['approval'] or review_digest(r['directory'])!=r['approval']['review_sha256']:
                raise SceneError('Component approval is no longer valid')
            if read_json(Path(r['directory'])/'validation.json')['implementation_sha256']!=implementation_hashes():
                raise SceneError('Approved behavior implementation has changed')
        self.coils=SafeCoilIO(client);self.verified=False

    @staticmethod
    def coil(address):
        match=re.fullmatch(r'%QX(\d+)\.([0-7])',address)
        if not match:raise SceneError('Invalid coil address')
        return int(match[1])*8+int(match[2])

    def read_word(self,address):
        try:response=self.client.read_holding_registers(address=address,count=1)
        except TypeError:response=self.client.read_holding_registers(address,1)
        if response is None or response.isError() or not response.registers:
            raise SceneError('PLC register read failed','plc_logic')
        return int(response.registers[0])

    def cycle(self,operator_inputs=None, *, substeps=1):
        if type(substeps) is not int or not 1<=substeps<=1000:raise SceneError('Invalid physics substeps')
        # Recheck identity every cycle; never drive another program accidentally.
        if self.read_word(20)!=self.prog_id:raise SceneError('PLC program identity mismatch','plc_logic')
        self.verified=True
        outputs={}
        for p in self.runtime.meta['io']:
            if p['dir']!='output':continue
            value=self.coils.read(self.coil(p['address'])) if p['type']=='BOOL' else self.read_word(int(p['address'][3:]))
            if p['type']=='INT' and p.get('range',[-32768])[0]<0 and value>32767:value-=65536
            outputs[p['name']]=value
        self.last_outputs=outputs
        feedback=self.runtime.step(outputs,operator_inputs)
        for _ in range(substeps-1):feedback=self.runtime.step(outputs)
        # Simulation owns its feedback. Operator contacts/faults are explicit local inputs.
        for p in self.runtime.meta['io']:
            if p['dir']!='input':continue
            value=feedback[p['name']]
            if p['type']=='BOOL':self.coils.write(self.coil(p['address']),bool(value))
            else:
                response=self.client.write_register(address=int(p['address'][3:]),value=int(value)&0xffff)
                if response is None or response.isError():raise SceneError('PLC feedback register write failed')
        return feedback


def run_frozen_scene(directory,host,port,prog_id,seconds,schedule=None,period_s=.02):
    """Wall-clock Modbus IO trace. Not a PLC acceptance verdict or lockstep clock."""
    import time
    from src.pipeline.modbus_io import connect
    from .device_scene import DeviceRuntime
    from src.agent.scene_agent.common import write_json
    directory=Path(directory)
    runtime=DeviceRuntime(read_json(directory/'scene.physical.json'),(directory/'scene.xml').read_text(encoding='utf-8'))
    if seconds<=0 or not .002<=period_s<=1:raise SceneError('Invalid run duration/period')
    ticks=period_s/runtime.model.opt.timestep
    if abs(ticks-round(ticks))>1e-8:raise SceneError('Period must be a multiple of physics timestep')
    events=sorted(schedule or [],key=lambda e:e['time_s'])
    for event in events:
        if event['time_s']<0:raise SceneError('Negative schedule time')
        for name,value in event['inputs'].items():
            p=runtime.points.get(name)
            if not p or p['writer'] not in ('operator','fault_harness'):raise SceneError('Schedule cannot spoof device feedback')
            runtime._check_value(p,value)
    # Check freeze validity before opening the network connection.
    DevicePlcLink(runtime,None,directory/'model.freeze.json',prog_id)
    client=connect(host,port);trace=[];index=0;started=time.monotonic();failure=None
    try:
        link=DevicePlcLink(runtime,client,directory/'model.freeze.json',prog_id)
        while runtime.data.time<seconds:
            updates={}
            while index<len(events) and events[index]['time_s']<=runtime.data.time+1e-9:
                updates.update(events[index]['inputs']);index+=1
            feedback=link.cycle(updates,substeps=round(ticks))
            trace.append({'simulation_time_s':float(runtime.data.time),'wall_time_s':time.monotonic()-started,
                          'outputs':dict(link.last_outputs),'inputs':feedback})
            delay=float(runtime.data.time)-(time.monotonic()-started)
            if delay>0:time.sleep(delay)
    except Exception as exc:
        failure={'layer':getattr(exc,'layer','plc_logic'),'error':str(exc)}
        raise
    finally:
        client.close()
        write_json(directory/'plc_io_trace.json',{'status':'failed' if failure else 'completed',
            'plc_acceptance':'not_run','clock':'wall_clock_polling_not_lockstep','failure':failure,
            'prog_id':prog_id,'definition_sha256':runtime.definition_sha256,'trace':trace,'events':runtime.events})
    return {'status':'completed','plc_acceptance':'not_run','trace':str(directory/'plc_io_trace.json')}

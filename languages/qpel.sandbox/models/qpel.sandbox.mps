<?xml version="1.0" encoding="UTF-8"?>
<model ref="r:98adf530-4eb9-438f-b8ef-068017f004a4(qpel.sandbox)">
  <persistence version="9" />
  <languages>
    <use id="413527eb-5550-4ad0-bc1b-796b7599a8f0" name="qpel" version="0" />
  </languages>
  <imports />
  <registry>
    <language id="413527eb-5550-4ad0-bc1b-796b7599a8f0" name="qpel">
      <concept id="709814182842351295" name="qpel.structure.Function" flags="ng" index="p0ZE2">
        <property id="709814182842351316" name="isKernel" index="p0ZFD" />
        <child id="709814182842351319" name="body" index="p0ZFE" />
        <child id="709814182842351318" name="parameters" index="p0ZFF" />
        <child id="709814182842502136" name="resultType" index="p1kZ5" />
        <child id="709814182842746871" name="domain" index="peofa" />
      </concept>
      <concept id="709814182842351294" name="qpel.structure.GpuModule" flags="ng" index="p0ZE3">
        <child id="709814182842351314" name="functions" index="p0ZFJ" />
      </concept>
      <concept id="709814182842351313" name="qpel.structure.Return" flags="ng" index="p0ZFG">
        <child id="709814182842351353" name="value" index="p0ZF4" />
      </concept>
      <concept id="709814182842351312" name="qpel.structure.While" flags="ng" index="p0ZFH">
        <child id="709814182842351352" name="body" index="p0ZF5" />
        <child id="709814182842351351" name="condition" index="p0ZFa" />
      </concept>
      <concept id="709814182842351309" name="qpel.structure.Assign" flags="ng" index="p0ZFK">
        <child id="709814182842351347" name="value" index="p0ZFe" />
        <child id="709814182842351346" name="target" index="p0ZFf" />
      </concept>
      <concept id="709814182842351308" name="qpel.structure.Variable" flags="ng" index="p0ZFL">
        <child id="709814182842351344" name="value" index="p0ZFd" />
        <child id="709814182842502144" name="type" index="p1kwX" />
      </concept>
      <concept id="709814182842351311" name="qpel.structure.If" flags="ng" index="p0ZFM">
        <child id="709814182842351349" name="condition" index="p0ZF8" />
        <child id="709814182842351350" name="thenBody" index="p0ZFb" />
      </concept>
      <concept id="709814182842351310" name="qpel.structure.Evaluate" flags="ng" index="p0ZFN">
        <child id="709814182842351348" name="value" index="p0ZF9" />
      </concept>
      <concept id="709814182842351305" name="qpel.structure.Call" flags="ng" index="p0ZFO">
        <reference id="709814182842746867" name="function" index="peofe" />
        <child id="709814182842351338" name="arguments" index="p0ZFn" />
      </concept>
      <concept id="709814182842351304" name="qpel.structure.Member" flags="ng" index="p0ZFP">
        <property id="709814182842351335" name="member" index="p0ZFq" />
        <child id="709814182842351336" name="base" index="p0ZFl" />
      </concept>
      <concept id="709814182842351307" name="qpel.structure.Block" flags="ng" index="p0ZFQ">
        <child id="709814182842351342" name="statements" index="p0ZFj" />
      </concept>
      <concept id="709814182842351306" name="qpel.structure.Select" flags="ng" index="p0ZFR">
        <child id="709814182842351341" name="no" index="p0ZFg" />
        <child id="709814182842351340" name="yes" index="p0ZFh" />
        <child id="709814182842351339" name="condition" index="p0ZFm" />
      </concept>
      <concept id="709814182842351301" name="qpel.structure.Binary" flags="ng" index="p0ZFS">
        <property id="709814182842351328" name="operator" index="p0ZFt" />
        <child id="709814182842351329" name="left" index="p0ZFs" />
        <child id="709814182842351330" name="right" index="p0ZFv" />
      </concept>
      <concept id="709814182842351300" name="qpel.structure.Literal" flags="ng" index="p0ZFT">
        <property id="709814182842351327" name="value" index="p0ZFy" />
      </concept>
      <concept id="709814182842351303" name="qpel.structure.Index" flags="ng" index="p0ZFU">
        <child id="709814182842351333" name="base" index="p0ZFo" />
        <child id="709814182842351334" name="index" index="p0ZFr" />
      </concept>
      <concept id="709814182842351302" name="qpel.structure.Unary" flags="ng" index="p0ZFV">
        <property id="709814182842351331" name="operator" index="p0ZFu" />
        <child id="709814182842351332" name="operand" index="p0ZFp" />
      </concept>
      <concept id="709814182842351296" name="qpel.structure.Parameter" flags="ng" index="p0ZFX">
        <property id="709814182842351324" name="isBuffer" index="p0ZFx" />
        <property id="709814182842351323" name="isGridIndex" index="p0ZFA" />
        <property id="709814182842351322" name="binding" index="p0ZFB" />
        <child id="709814182842502140" name="type" index="p1kZ1" />
      </concept>
      <concept id="709814182842351299" name="qpel.structure.Name" flags="ng" index="p0ZFY">
        <reference id="709814182842746863" name="declaration" index="peofi" />
      </concept>
      <concept id="709814182842497653" name="qpel.structure.AtomicAdd" flags="ng" index="p1rT8">
        <child id="709814182842497659" name="value" index="p1rT6" />
        <child id="709814182842497658" name="address" index="p1rT7" />
      </concept>
      <concept id="709814182842497652" name="qpel.structure.AtomicStore" flags="ng" index="p1rT9">
        <child id="709814182842497657" name="value" index="p1rT4" />
        <child id="709814182842497656" name="address" index="p1rT5" />
      </concept>
      <concept id="709814182842497654" name="qpel.structure.AtomicMin" flags="ng" index="p1rTb">
        <child id="709814182842497661" name="value" index="p1rT0" />
        <child id="709814182842497660" name="address" index="p1rT1" />
      </concept>
      <concept id="709814182842497649" name="qpel.structure.U32BufferType" flags="ng" index="p1rTc" />
      <concept id="709814182842497648" name="qpel.structure.AtomicU32BufferType" flags="ng" index="p1rTd" />
      <concept id="709814182842497651" name="qpel.structure.AtomicLoad" flags="ng" index="p1rTe">
        <child id="709814182842497655" name="address" index="p1rTa" />
      </concept>
      <concept id="709814182842497650" name="qpel.structure.Shape3DType" flags="ng" index="p1rTf" />
      <concept id="709814182842497645" name="qpel.structure.VoidType" flags="ng" index="p1rTg" />
      <concept id="709814182842497647" name="qpel.structure.ReadOnlyU8BufferType" flags="ng" index="p1rTi" />
      <concept id="709814182842497646" name="qpel.structure.U32Type" flags="ng" index="p1rTj" />
      <concept id="709814182842741224" name="qpel.structure.Binding" flags="ng" index="pevnl">
        <reference id="709814182842741241" name="parameter" index="pevn4" />
        <reference id="709814182842741242" name="resource" index="pevn7" />
      </concept>
      <concept id="709814182842741221" name="qpel.structure.Pipeline" flags="ng" index="pevno">
        <reference id="709814182842741231" name="module" index="pevni" />
        <child id="709814182842741229" name="resources" index="pevng" />
        <child id="709814182842741230" name="stages" index="pevnj" />
      </concept>
      <concept id="709814182842741220" name="qpel.structure.Maximum" flags="ng" index="pevnp">
        <child id="709814182842741228" name="right" index="pevnh" />
        <child id="709814182842741227" name="left" index="pevnm" />
      </concept>
      <concept id="709814182842741223" name="qpel.structure.Dispatch" flags="ng" index="pevnq">
        <property id="709814182842741237" name="completionBarrier" index="pevn8" />
        <property id="709814182842741236" name="blockSize" index="pevn9" />
        <reference id="709814182842741240" name="grid" index="pevn5" />
        <reference id="709814182842741239" name="kernel" index="pevna" />
        <child id="709814182842741238" name="bindings" index="pevnb" />
      </concept>
      <concept id="709814182842741222" name="qpel.structure.Resource" flags="ng" index="pevnr">
        <property id="709814182842741233" name="kind" index="pevnc" />
        <reference id="709814182843871080" name="extent" index="paaHl" />
        <child id="709814182842741234" name="type" index="pevnf" />
      </concept>
      <concept id="709814182842741217" name="qpel.structure.GridIndex" flags="ng" index="pevns" />
      <concept id="709814182842741219" name="qpel.structure.Minimum" flags="ng" index="pevnu">
        <child id="709814182842741225" name="left" index="pevnk" />
        <child id="709814182842741226" name="right" index="pevnn" />
      </concept>
      <concept id="709814182842741218" name="qpel.structure.Grid1D" flags="ng" index="pevnv" />
      <concept id="6420214654810501564" name="qpel.structure.F32Cast" flags="ng" index="wDpUa">
        <child id="6420214654810501566" name="operand" index="wDpU8" />
      </concept>
      <concept id="6420214654810501562" name="qpel.structure.F32BufferType" flags="ng" index="wDpUc" />
      <concept id="6420214654810501563" name="qpel.structure.F32Literal" flags="ng" index="wDpUd">
        <property id="6420214654810501565" name="value" index="wDpUb" />
      </concept>
      <concept id="6420214654810501560" name="qpel.structure.F32Type" flags="ng" index="wDpUe" />
      <concept id="6420214654810501561" name="qpel.structure.ReadOnlyF32BufferType" flags="ng" index="wDpUf" />
      <concept id="6420214654812805874" name="qpel.structure.F16BufferType" flags="ng" index="wLJn4" />
      <concept id="6420214654812805875" name="qpel.structure.F16Cast" flags="ng" index="wLJn5">
        <child id="6420214654812805876" name="operand" index="wLJn2" />
      </concept>
      <concept id="6420214654812805873" name="qpel.structure.ReadOnlyF16BufferType" flags="ng" index="wLJn7" />
      <concept id="6420214654812065282" name="qpel.structure.F32Fma" flags="ng" index="wNq4O">
        <child id="6420214654812065284" name="right" index="wNq4M" />
        <child id="6420214654812065285" name="addend" index="wNq4N" />
        <child id="6420214654812065283" name="left" index="wNq4P" />
      </concept>
      <concept id="6420214654814152860" name="qpel.structure.ReduceSumF32" flags="ng" index="wVkuE">
        <child id="6420214654814152862" name="start" index="wVkuC" />
        <child id="6420214654814152863" name="count" index="wVkuD" />
        <child id="6420214654814152861" name="buffer" index="wVkuF" />
      </concept>
      <concept id="6420214654815068173" name="qpel.structure.WorkgroupSumF32" flags="ng" index="z6OWV">
        <child id="6420214654815068174" name="value" index="z6OWS" />
      </concept>
    </language>
    <language id="ceab5195-25ea-4f22-9b92-103b95ca8c0c" name="jetbrains.mps.lang.core">
      <concept id="1169194658468" name="jetbrains.mps.lang.core.structure.INamedConcept" flags="ngI" index="TrEIO">
        <property id="1169194664001" name="name" index="TrG5h" />
      </concept>
    </language>
  </registry>
  <node concept="p0ZE3" id="BpL3OgQHKV">
    <property role="TrG5h" value="components" />
    <node concept="p0ZE2" id="BpL3OgQHKY" role="p0ZFJ">
      <property role="TrG5h" value="initialize" />
      <property role="p0ZFD" value="true" />
      <node concept="p0ZFX" id="BpL3OgQHL0" role="p0ZFF">
        <property role="TrG5h" value="mask" />
        <property role="p0ZFx" value="true" />
        <property role="p0ZFB" value="0" />
        <property role="p0ZFA" value="false" />
        <node concept="p1rTi" id="BpL3OgRsg4" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3OgQHL1" role="p0ZFF">
        <property role="TrG5h" value="parent" />
        <property role="p0ZFx" value="true" />
        <property role="p0ZFB" value="1" />
        <property role="p0ZFA" value="false" />
        <node concept="p1rTd" id="BpL3OgRsg5" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3OgQHL2" role="p0ZFF">
        <property role="TrG5h" value="counts" />
        <property role="p0ZFx" value="true" />
        <property role="p0ZFB" value="2" />
        <property role="p0ZFA" value="false" />
        <node concept="p1rTd" id="BpL3OgRsg6" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3OgQHL3" role="p0ZFF">
        <property role="TrG5h" value="shape" />
        <property role="p0ZFx" value="true" />
        <property role="p0ZFB" value="3" />
        <property role="p0ZFA" value="false" />
        <node concept="p1rTf" id="BpL3OgRsg7" role="p1kZ1" />
      </node>
      <node concept="p0ZFQ" id="BpL3OgQHL5" role="p0ZFE">
        <node concept="p0ZFM" id="BpL3OgQHL6" role="p0ZFj">
          <node concept="p0ZFS" id="BpL3OgQHL9" role="p0ZF8">
            <property role="p0ZFt" value="&gt;=" />
            <node concept="pevns" id="BpL3OgWCCd" role="p0ZFs" />
            <node concept="p0ZFS" id="BpL3OgQHLd" role="p0ZFv">
              <property role="p0ZFt" value="*" />
              <node concept="p0ZFS" id="BpL3OgQHLg" role="p0ZFs">
                <property role="p0ZFt" value="*" />
                <node concept="p0ZFP" id="BpL3OgQHLj" role="p0ZFs">
                  <property role="p0ZFq" value="x" />
                  <node concept="p0ZFY" id="BpL3OgQHLl" role="p0ZFl">
                    <ref role="peofi" node="BpL3OgQHL3" />
                  </node>
                </node>
                <node concept="p0ZFP" id="BpL3OgQHLm" role="p0ZFv">
                  <property role="p0ZFq" value="y" />
                  <node concept="p0ZFY" id="BpL3OgQHLo" role="p0ZFl">
                    <ref role="peofi" node="BpL3OgQHL3" />
                  </node>
                </node>
              </node>
              <node concept="p0ZFP" id="BpL3OgQHLp" role="p0ZFv">
                <property role="p0ZFq" value="z" />
                <node concept="p0ZFY" id="BpL3OgQHLr" role="p0ZFl">
                  <ref role="peofi" node="BpL3OgQHL3" />
                </node>
              </node>
            </node>
          </node>
          <node concept="p0ZFQ" id="BpL3OgQHLs" role="p0ZFb">
            <node concept="p0ZFG" id="BpL3OgQHLt" role="p0ZFj" />
          </node>
        </node>
        <node concept="p0ZFN" id="BpL3OgQHLu" role="p0ZFj">
          <node concept="p1rT9" id="BpL3OgRs4M" role="p0ZF9">
            <node concept="p0ZFS" id="BpL3OgRs4P" role="p1rT5">
              <property role="p0ZFt" value="+" />
              <node concept="p0ZFY" id="BpL3OgRs4S" role="p0ZFs">
                <ref role="peofi" node="BpL3OgQHL1" />
              </node>
              <node concept="pevns" id="BpL3OgWCCe" role="p0ZFv" />
            </node>
            <node concept="pevns" id="BpL3OgWCCf" role="p1rT4" />
          </node>
        </node>
        <node concept="p0ZFN" id="BpL3OgQHLC" role="p0ZFj">
          <node concept="p1rT9" id="BpL3OgRs4V" role="p0ZF9">
            <node concept="p0ZFS" id="BpL3OgRs4Y" role="p1rT5">
              <property role="p0ZFt" value="+" />
              <node concept="p0ZFY" id="BpL3OgRs51" role="p0ZFs">
                <ref role="peofi" node="BpL3OgQHL2" />
              </node>
              <node concept="pevns" id="BpL3OgWCCg" role="p0ZFv" />
            </node>
            <node concept="p0ZFT" id="BpL3OgRs53" role="p1rT4">
              <property role="p0ZFy" value="0u" />
            </node>
          </node>
        </node>
      </node>
      <node concept="p1rTg" id="BpL3OgRs5V" role="p1kZ5" />
      <node concept="pevnv" id="BpL3OgWCCc" role="peofa" />
    </node>
    <node concept="p0ZE2" id="BpL3OgQHLM" role="p0ZFJ">
      <property role="TrG5h" value="root" />
      <property role="p0ZFD" value="false" />
      <node concept="p0ZFX" id="BpL3OgQHLO" role="p0ZFF">
        <property role="TrG5h" value="p" />
        <property role="p0ZFx" value="false" />
        <property role="p0ZFB" value="0" />
        <property role="p0ZFA" value="false" />
        <node concept="p1rTd" id="BpL3OgRsg9" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3OgQHLP" role="p0ZFF">
        <property role="TrG5h" value="i" />
        <property role="p0ZFx" value="false" />
        <property role="p0ZFB" value="0" />
        <property role="p0ZFA" value="false" />
        <node concept="p1rTj" id="BpL3OgRsga" role="p1kZ1" />
      </node>
      <node concept="p0ZFQ" id="BpL3OgQHLQ" role="p0ZFE">
        <node concept="p0ZFL" id="BpL3OgQHLR" role="p0ZFj">
          <property role="TrG5h" value="next" />
          <node concept="p1rTe" id="BpL3OgRs54" role="p0ZFd">
            <node concept="p0ZFS" id="BpL3OgRs56" role="p1rTa">
              <property role="p0ZFt" value="+" />
              <node concept="p0ZFY" id="BpL3OgRs59" role="p0ZFs">
                <ref role="peofi" node="BpL3OgQHLO" />
              </node>
              <node concept="p0ZFY" id="BpL3OgRs5a" role="p0ZFv">
                <ref role="peofi" node="BpL3OgQHLP" />
              </node>
            </node>
          </node>
          <node concept="p1rTj" id="BpL3OgRsgb" role="p1kwX" />
        </node>
        <node concept="p0ZFH" id="BpL3OgQHM0" role="p0ZFj">
          <node concept="p0ZFS" id="BpL3OgQHM3" role="p0ZFa">
            <property role="p0ZFt" value="!=" />
            <node concept="p0ZFY" id="BpL3OgQHM6" role="p0ZFs">
              <ref role="peofi" node="BpL3OgQHLR" />
            </node>
            <node concept="p0ZFY" id="BpL3OgQHM7" role="p0ZFv">
              <ref role="peofi" node="BpL3OgQHLP" />
            </node>
          </node>
          <node concept="p0ZFQ" id="BpL3OgQHM8" role="p0ZF5">
            <node concept="p0ZFK" id="BpL3OgQHM9" role="p0ZFj">
              <node concept="p0ZFY" id="BpL3OgQHMc" role="p0ZFf">
                <ref role="peofi" node="BpL3OgQHLP" />
              </node>
              <node concept="p0ZFY" id="BpL3OgQHMd" role="p0ZFe">
                <ref role="peofi" node="BpL3OgQHLR" />
              </node>
            </node>
            <node concept="p0ZFK" id="BpL3OgQHMe" role="p0ZFj">
              <node concept="p0ZFY" id="BpL3OgQHMh" role="p0ZFf">
                <ref role="peofi" node="BpL3OgQHLR" />
              </node>
              <node concept="p1rTe" id="BpL3OgRs5b" role="p0ZFe">
                <node concept="p0ZFS" id="BpL3OgRs5d" role="p1rTa">
                  <property role="p0ZFt" value="+" />
                  <node concept="p0ZFY" id="BpL3OgRs5g" role="p0ZFs">
                    <ref role="peofi" node="BpL3OgQHLO" />
                  </node>
                  <node concept="p0ZFY" id="BpL3OgRs5h" role="p0ZFv">
                    <ref role="peofi" node="BpL3OgQHLP" />
                  </node>
                </node>
              </node>
            </node>
          </node>
        </node>
        <node concept="p0ZFG" id="BpL3OgQHMp" role="p0ZFj">
          <node concept="p0ZFY" id="BpL3OgQHMq" role="p0ZF4">
            <ref role="peofi" node="BpL3OgQHLP" />
          </node>
        </node>
      </node>
      <node concept="p1rTj" id="BpL3OgRsg8" role="p1kZ5" />
    </node>
    <node concept="p0ZE2" id="BpL3OgQHMr" role="p0ZFJ">
      <property role="TrG5h" value="unite" />
      <property role="p0ZFD" value="false" />
      <node concept="p0ZFX" id="BpL3OgQHMt" role="p0ZFF">
        <property role="TrG5h" value="p" />
        <property role="p0ZFx" value="false" />
        <property role="p0ZFB" value="0" />
        <property role="p0ZFA" value="false" />
        <node concept="p1rTd" id="BpL3OgRsgd" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3OgQHMu" role="p0ZFF">
        <property role="TrG5h" value="a" />
        <property role="p0ZFx" value="false" />
        <property role="p0ZFB" value="0" />
        <property role="p0ZFA" value="false" />
        <node concept="p1rTj" id="BpL3OgRsge" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3OgQHMv" role="p0ZFF">
        <property role="TrG5h" value="b" />
        <property role="p0ZFx" value="false" />
        <property role="p0ZFB" value="0" />
        <property role="p0ZFA" value="false" />
        <node concept="p1rTj" id="BpL3OgRsgf" role="p1kZ1" />
      </node>
      <node concept="p0ZFQ" id="BpL3OgQHMw" role="p0ZFE">
        <node concept="p0ZFH" id="BpL3OgQHMx" role="p0ZFj">
          <node concept="p0ZFT" id="BpL3OgQHM$" role="p0ZFa">
            <property role="p0ZFy" value="true" />
          </node>
          <node concept="p0ZFQ" id="BpL3OgQHM_" role="p0ZF5">
            <node concept="p0ZFK" id="BpL3OgQHMA" role="p0ZFj">
              <node concept="p0ZFY" id="BpL3OgQHMD" role="p0ZFf">
                <ref role="peofi" node="BpL3OgQHMu" />
              </node>
              <node concept="p0ZFO" id="BpL3OgQHME" role="p0ZFe">
                <ref role="peofe" node="BpL3OgQHLM" />
                <node concept="p0ZFY" id="BpL3OgQHMF" role="p0ZFn">
                  <ref role="peofi" node="BpL3OgQHMt" />
                </node>
                <node concept="p0ZFY" id="BpL3OgQHMG" role="p0ZFn">
                  <ref role="peofi" node="BpL3OgQHMu" />
                </node>
              </node>
            </node>
            <node concept="p0ZFK" id="BpL3OgQHMH" role="p0ZFj">
              <node concept="p0ZFY" id="BpL3OgQHMK" role="p0ZFf">
                <ref role="peofi" node="BpL3OgQHMv" />
              </node>
              <node concept="p0ZFO" id="BpL3OgQHML" role="p0ZFe">
                <ref role="peofe" node="BpL3OgQHLM" />
                <node concept="p0ZFY" id="BpL3OgQHMM" role="p0ZFn">
                  <ref role="peofi" node="BpL3OgQHMt" />
                </node>
                <node concept="p0ZFY" id="BpL3OgQHMN" role="p0ZFn">
                  <ref role="peofi" node="BpL3OgQHMv" />
                </node>
              </node>
            </node>
            <node concept="p0ZFM" id="BpL3OgQHMO" role="p0ZFj">
              <node concept="p0ZFS" id="BpL3OgQHMR" role="p0ZF8">
                <property role="p0ZFt" value="==" />
                <node concept="p0ZFY" id="BpL3OgQHMU" role="p0ZFs">
                  <ref role="peofi" node="BpL3OgQHMu" />
                </node>
                <node concept="p0ZFY" id="BpL3OgQHMV" role="p0ZFv">
                  <ref role="peofi" node="BpL3OgQHMv" />
                </node>
              </node>
              <node concept="p0ZFQ" id="BpL3OgQHMW" role="p0ZFb">
                <node concept="p0ZFG" id="BpL3OgQHMX" role="p0ZFj" />
              </node>
            </node>
            <node concept="p0ZFL" id="BpL3OgQHMY" role="p0ZFj">
              <property role="TrG5h" value="lo" />
              <node concept="pevnu" id="BpL3OgWCCH" role="p0ZFd">
                <node concept="p0ZFY" id="BpL3OgWCCK" role="pevnk">
                  <ref role="peofi" node="BpL3OgQHMu" resolve="a" />
                </node>
                <node concept="p0ZFY" id="BpL3OgWCCL" role="pevnn">
                  <ref role="peofi" node="BpL3OgQHMv" resolve="b" />
                </node>
              </node>
              <node concept="p1rTj" id="BpL3OgRsgg" role="p1kwX" />
            </node>
            <node concept="p0ZFL" id="BpL3OgQHN3" role="p0ZFj">
              <property role="TrG5h" value="hi" />
              <node concept="pevnp" id="BpL3OgWCCM" role="p0ZFd">
                <node concept="p0ZFY" id="BpL3OgWCCP" role="pevnm">
                  <ref role="peofi" node="BpL3OgQHMu" resolve="a" />
                </node>
                <node concept="p0ZFY" id="BpL3OgWCCQ" role="pevnh">
                  <ref role="peofi" node="BpL3OgQHMv" resolve="b" />
                </node>
              </node>
              <node concept="p1rTj" id="BpL3OgRsgh" role="p1kwX" />
            </node>
            <node concept="p0ZFL" id="BpL3OgQHN8" role="p0ZFj">
              <property role="TrG5h" value="old" />
              <node concept="p1rTb" id="BpL3OgRs5i" role="p0ZFd">
                <node concept="p0ZFS" id="BpL3OgRs5l" role="p1rT1">
                  <property role="p0ZFt" value="+" />
                  <node concept="p0ZFY" id="BpL3OgRs5o" role="p0ZFs">
                    <ref role="peofi" node="BpL3OgQHMt" />
                  </node>
                  <node concept="p0ZFY" id="BpL3OgRs5p" role="p0ZFv">
                    <ref role="peofi" node="BpL3OgQHN3" />
                  </node>
                </node>
                <node concept="p0ZFY" id="BpL3OgRs5q" role="p1rT0">
                  <ref role="peofi" node="BpL3OgQHMY" />
                </node>
              </node>
              <node concept="p1rTj" id="BpL3OgRsgi" role="p1kwX" />
            </node>
            <node concept="p0ZFM" id="BpL3OgQHNi" role="p0ZFj">
              <node concept="p0ZFS" id="BpL3OgQHNl" role="p0ZF8">
                <property role="p0ZFt" value="==" />
                <node concept="p0ZFY" id="BpL3OgQHNo" role="p0ZFs">
                  <ref role="peofi" node="BpL3OgQHN8" />
                </node>
                <node concept="p0ZFY" id="BpL3OgQHNp" role="p0ZFv">
                  <ref role="peofi" node="BpL3OgQHN3" />
                </node>
              </node>
              <node concept="p0ZFQ" id="BpL3OgQHNq" role="p0ZFb">
                <node concept="p0ZFG" id="BpL3OgQHNr" role="p0ZFj" />
              </node>
            </node>
            <node concept="p0ZFK" id="BpL3OgQHNs" role="p0ZFj">
              <node concept="p0ZFY" id="BpL3OgQHNv" role="p0ZFf">
                <ref role="peofi" node="BpL3OgQHMu" />
              </node>
              <node concept="p0ZFY" id="BpL3OgQHNw" role="p0ZFe">
                <ref role="peofi" node="BpL3OgQHN8" />
              </node>
            </node>
            <node concept="p0ZFK" id="BpL3OgQHNx" role="p0ZFj">
              <node concept="p0ZFY" id="BpL3OgQHN$" role="p0ZFf">
                <ref role="peofi" node="BpL3OgQHMv" />
              </node>
              <node concept="p0ZFY" id="BpL3OgQHN_" role="p0ZFe">
                <ref role="peofi" node="BpL3OgQHMY" />
              </node>
            </node>
          </node>
        </node>
      </node>
      <node concept="p1rTg" id="BpL3OgRsgc" role="p1kZ5" />
    </node>
    <node concept="p0ZE2" id="BpL3OgQHNA" role="p0ZFJ">
      <property role="TrG5h" value="merge" />
      <property role="p0ZFD" value="true" />
      <node concept="p0ZFX" id="BpL3OgQHNC" role="p0ZFF">
        <property role="TrG5h" value="mask" />
        <property role="p0ZFx" value="true" />
        <property role="p0ZFB" value="0" />
        <property role="p0ZFA" value="false" />
        <node concept="p1rTi" id="BpL3OgRsgk" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3OgQHND" role="p0ZFF">
        <property role="TrG5h" value="parent" />
        <property role="p0ZFx" value="true" />
        <property role="p0ZFB" value="1" />
        <property role="p0ZFA" value="false" />
        <node concept="p1rTd" id="BpL3OgRsgl" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3OgQHNE" role="p0ZFF">
        <property role="TrG5h" value="counts" />
        <property role="p0ZFx" value="true" />
        <property role="p0ZFB" value="2" />
        <property role="p0ZFA" value="false" />
        <node concept="p1rTd" id="BpL3OgRsgm" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3OgQHNF" role="p0ZFF">
        <property role="TrG5h" value="shape" />
        <property role="p0ZFx" value="true" />
        <property role="p0ZFB" value="3" />
        <property role="p0ZFA" value="false" />
        <node concept="p1rTf" id="BpL3OgRsgn" role="p1kZ1" />
      </node>
      <node concept="p0ZFQ" id="BpL3OgQHNH" role="p0ZFE">
        <node concept="p0ZFL" id="BpL3OgQHNI" role="p0ZFj">
          <property role="TrG5h" value="w" />
          <node concept="p0ZFP" id="BpL3OgQHNK" role="p0ZFd">
            <property role="p0ZFq" value="x" />
            <node concept="p0ZFY" id="BpL3OgQHNM" role="p0ZFl">
              <ref role="peofi" node="BpL3OgQHNF" />
            </node>
          </node>
          <node concept="p1rTj" id="BpL3OgRsgo" role="p1kwX" />
        </node>
        <node concept="p0ZFL" id="BpL3OgQHNN" role="p0ZFj">
          <property role="TrG5h" value="h" />
          <node concept="p0ZFP" id="BpL3OgQHNP" role="p0ZFd">
            <property role="p0ZFq" value="y" />
            <node concept="p0ZFY" id="BpL3OgQHNR" role="p0ZFl">
              <ref role="peofi" node="BpL3OgQHNF" />
            </node>
          </node>
          <node concept="p1rTj" id="BpL3OgRsgp" role="p1kwX" />
        </node>
        <node concept="p0ZFM" id="BpL3OgQHNS" role="p0ZFj">
          <node concept="p0ZFS" id="BpL3OgQHNV" role="p0ZF8">
            <property role="p0ZFt" value="||" />
            <node concept="p0ZFS" id="BpL3OgQHNY" role="p0ZFs">
              <property role="p0ZFt" value="&gt;=" />
              <node concept="pevns" id="BpL3OgWCCi" role="p0ZFs" />
              <node concept="p0ZFS" id="BpL3OgQHO2" role="p0ZFv">
                <property role="p0ZFt" value="*" />
                <node concept="p0ZFS" id="BpL3OgQHO5" role="p0ZFs">
                  <property role="p0ZFt" value="*" />
                  <node concept="p0ZFY" id="BpL3OgQHO8" role="p0ZFs">
                    <ref role="peofi" node="BpL3OgQHNI" />
                  </node>
                  <node concept="p0ZFY" id="BpL3OgQHO9" role="p0ZFv">
                    <ref role="peofi" node="BpL3OgQHNN" />
                  </node>
                </node>
                <node concept="p0ZFP" id="BpL3OgQHOa" role="p0ZFv">
                  <property role="p0ZFq" value="z" />
                  <node concept="p0ZFY" id="BpL3OgQHOc" role="p0ZFl">
                    <ref role="peofi" node="BpL3OgQHNF" />
                  </node>
                </node>
              </node>
            </node>
            <node concept="p0ZFV" id="BpL3OgQHOd" role="p0ZFv">
              <property role="p0ZFu" value="!" />
              <node concept="p0ZFU" id="BpL3OgQHOf" role="p0ZFp">
                <node concept="p0ZFY" id="BpL3OgQHOi" role="p0ZFo">
                  <ref role="peofi" node="BpL3OgQHNC" />
                </node>
                <node concept="pevns" id="BpL3OgWCCj" role="p0ZFr" />
              </node>
            </node>
          </node>
          <node concept="p0ZFQ" id="BpL3OgQHOk" role="p0ZFb">
            <node concept="p0ZFG" id="BpL3OgQHOl" role="p0ZFj" />
          </node>
        </node>
        <node concept="p0ZFL" id="BpL3OgQHOm" role="p0ZFj">
          <property role="TrG5h" value="x" />
          <node concept="p0ZFS" id="BpL3OgQHOo" role="p0ZFd">
            <property role="p0ZFt" value="%" />
            <node concept="pevns" id="BpL3OgWCCk" role="p0ZFs" />
            <node concept="p0ZFY" id="BpL3OgQHOs" role="p0ZFv">
              <ref role="peofi" node="BpL3OgQHNI" />
            </node>
          </node>
          <node concept="p1rTj" id="BpL3OgRsgq" role="p1kwX" />
        </node>
        <node concept="p0ZFL" id="BpL3OgQHOt" role="p0ZFj">
          <property role="TrG5h" value="y" />
          <node concept="p0ZFS" id="BpL3OgQHOv" role="p0ZFd">
            <property role="p0ZFt" value="%" />
            <node concept="p0ZFS" id="BpL3OgQHOy" role="p0ZFs">
              <property role="p0ZFt" value="/" />
              <node concept="pevns" id="BpL3OgWCCl" role="p0ZFs" />
              <node concept="p0ZFY" id="BpL3OgQHOA" role="p0ZFv">
                <ref role="peofi" node="BpL3OgQHNI" />
              </node>
            </node>
            <node concept="p0ZFY" id="BpL3OgQHOB" role="p0ZFv">
              <ref role="peofi" node="BpL3OgQHNN" />
            </node>
          </node>
          <node concept="p1rTj" id="BpL3OgRsgr" role="p1kwX" />
        </node>
        <node concept="p0ZFM" id="BpL3OgQHOC" role="p0ZFj">
          <node concept="p0ZFS" id="BpL3OgQHOF" role="p0ZF8">
            <property role="p0ZFt" value="&amp;&amp;" />
            <node concept="p0ZFS" id="BpL3OgQHOI" role="p0ZFs">
              <property role="p0ZFt" value="&gt;" />
              <node concept="p0ZFY" id="BpL3OgQHOL" role="p0ZFs">
                <ref role="peofi" node="BpL3OgQHOm" />
              </node>
              <node concept="p0ZFT" id="BpL3OgQHOM" role="p0ZFv">
                <property role="p0ZFy" value="0" />
              </node>
            </node>
            <node concept="p0ZFU" id="BpL3OgQHON" role="p0ZFv">
              <node concept="p0ZFY" id="BpL3OgQHOQ" role="p0ZFo">
                <ref role="peofi" node="BpL3OgQHNC" />
              </node>
              <node concept="p0ZFS" id="BpL3OgQHOR" role="p0ZFr">
                <property role="p0ZFt" value="-" />
                <node concept="pevns" id="BpL3OgWCCm" role="p0ZFs" />
                <node concept="p0ZFT" id="BpL3OgQHOV" role="p0ZFv">
                  <property role="p0ZFy" value="1" />
                </node>
              </node>
            </node>
          </node>
          <node concept="p0ZFQ" id="BpL3OgQHOW" role="p0ZFb">
            <node concept="p0ZFN" id="BpL3OgQHOX" role="p0ZFj">
              <node concept="p0ZFO" id="BpL3OgQHOZ" role="p0ZF9">
                <ref role="peofe" node="BpL3OgQHMr" />
                <node concept="p0ZFY" id="BpL3OgQHP0" role="p0ZFn">
                  <ref role="peofi" node="BpL3OgQHND" />
                </node>
                <node concept="pevns" id="BpL3OgWCCn" role="p0ZFn" />
                <node concept="p0ZFS" id="BpL3OgQHP2" role="p0ZFn">
                  <property role="p0ZFt" value="-" />
                  <node concept="pevns" id="BpL3OgWCCo" role="p0ZFs" />
                  <node concept="p0ZFT" id="BpL3OgQHP6" role="p0ZFv">
                    <property role="p0ZFy" value="1" />
                  </node>
                </node>
              </node>
            </node>
          </node>
        </node>
        <node concept="p0ZFM" id="BpL3OgQHP7" role="p0ZFj">
          <node concept="p0ZFS" id="BpL3OgQHPa" role="p0ZF8">
            <property role="p0ZFt" value="&gt;" />
            <node concept="p0ZFY" id="BpL3OgQHPd" role="p0ZFs">
              <ref role="peofi" node="BpL3OgQHOt" />
            </node>
            <node concept="p0ZFT" id="BpL3OgQHPe" role="p0ZFv">
              <property role="p0ZFy" value="0" />
            </node>
          </node>
          <node concept="p0ZFQ" id="BpL3OgQHPf" role="p0ZFb">
            <node concept="p0ZFM" id="BpL3OgQHPg" role="p0ZFj">
              <node concept="p0ZFU" id="BpL3OgQHPj" role="p0ZF8">
                <node concept="p0ZFY" id="BpL3OgQHPm" role="p0ZFo">
                  <ref role="peofi" node="BpL3OgQHNC" />
                </node>
                <node concept="p0ZFS" id="BpL3OgQHPn" role="p0ZFr">
                  <property role="p0ZFt" value="-" />
                  <node concept="pevns" id="BpL3OgWCCp" role="p0ZFs" />
                  <node concept="p0ZFY" id="BpL3OgQHPr" role="p0ZFv">
                    <ref role="peofi" node="BpL3OgQHNI" />
                  </node>
                </node>
              </node>
              <node concept="p0ZFQ" id="BpL3OgQHPs" role="p0ZFb">
                <node concept="p0ZFN" id="BpL3OgQHPt" role="p0ZFj">
                  <node concept="p0ZFO" id="BpL3OgQHPv" role="p0ZF9">
                    <ref role="peofe" node="BpL3OgQHMr" />
                    <node concept="p0ZFY" id="BpL3OgQHPw" role="p0ZFn">
                      <ref role="peofi" node="BpL3OgQHND" />
                    </node>
                    <node concept="pevns" id="BpL3OgWCCq" role="p0ZFn" />
                    <node concept="p0ZFS" id="BpL3OgQHPy" role="p0ZFn">
                      <property role="p0ZFt" value="-" />
                      <node concept="pevns" id="BpL3OgWCCr" role="p0ZFs" />
                      <node concept="p0ZFY" id="BpL3OgQHPA" role="p0ZFv">
                        <ref role="peofi" node="BpL3OgQHNI" />
                      </node>
                    </node>
                  </node>
                </node>
              </node>
            </node>
            <node concept="p0ZFM" id="BpL3OgQHPB" role="p0ZFj">
              <node concept="p0ZFS" id="BpL3OgQHPE" role="p0ZF8">
                <property role="p0ZFt" value="&amp;&amp;" />
                <node concept="p0ZFS" id="BpL3OgQHPH" role="p0ZFs">
                  <property role="p0ZFt" value="&gt;" />
                  <node concept="p0ZFY" id="BpL3OgQHPK" role="p0ZFs">
                    <ref role="peofi" node="BpL3OgQHOm" />
                  </node>
                  <node concept="p0ZFT" id="BpL3OgQHPL" role="p0ZFv">
                    <property role="p0ZFy" value="0" />
                  </node>
                </node>
                <node concept="p0ZFU" id="BpL3OgQHPM" role="p0ZFv">
                  <node concept="p0ZFY" id="BpL3OgQHPP" role="p0ZFo">
                    <ref role="peofi" node="BpL3OgQHNC" />
                  </node>
                  <node concept="p0ZFS" id="BpL3OgQHPQ" role="p0ZFr">
                    <property role="p0ZFt" value="-" />
                    <node concept="p0ZFS" id="BpL3OgQHPT" role="p0ZFs">
                      <property role="p0ZFt" value="-" />
                      <node concept="pevns" id="BpL3OgWCCs" role="p0ZFs" />
                      <node concept="p0ZFY" id="BpL3OgQHPX" role="p0ZFv">
                        <ref role="peofi" node="BpL3OgQHNI" />
                      </node>
                    </node>
                    <node concept="p0ZFT" id="BpL3OgQHPY" role="p0ZFv">
                      <property role="p0ZFy" value="1" />
                    </node>
                  </node>
                </node>
              </node>
              <node concept="p0ZFQ" id="BpL3OgQHPZ" role="p0ZFb">
                <node concept="p0ZFN" id="BpL3OgQHQ0" role="p0ZFj">
                  <node concept="p0ZFO" id="BpL3OgQHQ2" role="p0ZF9">
                    <ref role="peofe" node="BpL3OgQHMr" />
                    <node concept="p0ZFY" id="BpL3OgQHQ3" role="p0ZFn">
                      <ref role="peofi" node="BpL3OgQHND" />
                    </node>
                    <node concept="pevns" id="BpL3OgWCCt" role="p0ZFn" />
                    <node concept="p0ZFS" id="BpL3OgQHQ5" role="p0ZFn">
                      <property role="p0ZFt" value="-" />
                      <node concept="p0ZFS" id="BpL3OgQHQ8" role="p0ZFs">
                        <property role="p0ZFt" value="-" />
                        <node concept="pevns" id="BpL3OgWCCu" role="p0ZFs" />
                        <node concept="p0ZFY" id="BpL3OgQHQc" role="p0ZFv">
                          <ref role="peofi" node="BpL3OgQHNI" />
                        </node>
                      </node>
                      <node concept="p0ZFT" id="BpL3OgQHQd" role="p0ZFv">
                        <property role="p0ZFy" value="1" />
                      </node>
                    </node>
                  </node>
                </node>
              </node>
            </node>
            <node concept="p0ZFM" id="BpL3OgQHQe" role="p0ZFj">
              <node concept="p0ZFS" id="BpL3OgQHQh" role="p0ZF8">
                <property role="p0ZFt" value="&amp;&amp;" />
                <node concept="p0ZFS" id="BpL3OgQHQk" role="p0ZFs">
                  <property role="p0ZFt" value="&lt;" />
                  <node concept="p0ZFS" id="BpL3OgQHQn" role="p0ZFs">
                    <property role="p0ZFt" value="+" />
                    <node concept="p0ZFY" id="BpL3OgQHQq" role="p0ZFs">
                      <ref role="peofi" node="BpL3OgQHOm" />
                    </node>
                    <node concept="p0ZFT" id="BpL3OgQHQr" role="p0ZFv">
                      <property role="p0ZFy" value="1" />
                    </node>
                  </node>
                  <node concept="p0ZFY" id="BpL3OgQHQs" role="p0ZFv">
                    <ref role="peofi" node="BpL3OgQHNI" />
                  </node>
                </node>
                <node concept="p0ZFU" id="BpL3OgQHQt" role="p0ZFv">
                  <node concept="p0ZFY" id="BpL3OgQHQw" role="p0ZFo">
                    <ref role="peofi" node="BpL3OgQHNC" />
                  </node>
                  <node concept="p0ZFS" id="BpL3OgQHQx" role="p0ZFr">
                    <property role="p0ZFt" value="+" />
                    <node concept="p0ZFS" id="BpL3OgQHQ$" role="p0ZFs">
                      <property role="p0ZFt" value="-" />
                      <node concept="pevns" id="BpL3OgWCCv" role="p0ZFs" />
                      <node concept="p0ZFY" id="BpL3OgQHQC" role="p0ZFv">
                        <ref role="peofi" node="BpL3OgQHNI" />
                      </node>
                    </node>
                    <node concept="p0ZFT" id="BpL3OgQHQD" role="p0ZFv">
                      <property role="p0ZFy" value="1" />
                    </node>
                  </node>
                </node>
              </node>
              <node concept="p0ZFQ" id="BpL3OgQHQE" role="p0ZFb">
                <node concept="p0ZFN" id="BpL3OgQHQF" role="p0ZFj">
                  <node concept="p0ZFO" id="BpL3OgQHQH" role="p0ZF9">
                    <ref role="peofe" node="BpL3OgQHMr" />
                    <node concept="p0ZFY" id="BpL3OgQHQI" role="p0ZFn">
                      <ref role="peofi" node="BpL3OgQHND" />
                    </node>
                    <node concept="pevns" id="BpL3OgWCCw" role="p0ZFn" />
                    <node concept="p0ZFS" id="BpL3OgQHQK" role="p0ZFn">
                      <property role="p0ZFt" value="+" />
                      <node concept="p0ZFS" id="BpL3OgQHQN" role="p0ZFs">
                        <property role="p0ZFt" value="-" />
                        <node concept="pevns" id="BpL3OgWCCx" role="p0ZFs" />
                        <node concept="p0ZFY" id="BpL3OgQHQR" role="p0ZFv">
                          <ref role="peofi" node="BpL3OgQHNI" />
                        </node>
                      </node>
                      <node concept="p0ZFT" id="BpL3OgQHQS" role="p0ZFv">
                        <property role="p0ZFy" value="1" />
                      </node>
                    </node>
                  </node>
                </node>
              </node>
            </node>
          </node>
        </node>
      </node>
      <node concept="p1rTg" id="BpL3OgRsgj" role="p1kZ5" />
      <node concept="pevnv" id="BpL3OgWCCh" role="peofa" />
    </node>
    <node concept="p0ZE2" id="BpL3OgQHQT" role="p0ZFJ">
      <property role="TrG5h" value="label_count" />
      <property role="p0ZFD" value="true" />
      <node concept="p0ZFX" id="BpL3OgQHQV" role="p0ZFF">
        <property role="TrG5h" value="mask" />
        <property role="p0ZFx" value="true" />
        <property role="p0ZFB" value="0" />
        <property role="p0ZFA" value="false" />
        <node concept="p1rTi" id="BpL3OgRsgt" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3OgQHQW" role="p0ZFF">
        <property role="TrG5h" value="parent" />
        <property role="p0ZFx" value="true" />
        <property role="p0ZFB" value="1" />
        <property role="p0ZFA" value="false" />
        <node concept="p1rTd" id="BpL3OgRsgu" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3OgQHQX" role="p0ZFF">
        <property role="TrG5h" value="counts" />
        <property role="p0ZFx" value="true" />
        <property role="p0ZFB" value="2" />
        <property role="p0ZFA" value="false" />
        <node concept="p1rTd" id="BpL3OgRsgv" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3OgQHQY" role="p0ZFF">
        <property role="TrG5h" value="shape" />
        <property role="p0ZFx" value="true" />
        <property role="p0ZFB" value="3" />
        <property role="p0ZFA" value="false" />
        <node concept="p1rTf" id="BpL3OgRsgw" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3OgQHQZ" role="p0ZFF">
        <property role="TrG5h" value="labels" />
        <property role="p0ZFx" value="true" />
        <property role="p0ZFB" value="4" />
        <property role="p0ZFA" value="false" />
        <node concept="p1rTc" id="BpL3OgRsgx" role="p1kZ1" />
      </node>
      <node concept="p0ZFQ" id="BpL3OgQHR1" role="p0ZFE">
        <node concept="p0ZFM" id="BpL3OgQHR2" role="p0ZFj">
          <node concept="p0ZFS" id="BpL3OgQHR5" role="p0ZF8">
            <property role="p0ZFt" value="&gt;=" />
            <node concept="pevns" id="BpL3OgWCCz" role="p0ZFs" />
            <node concept="p0ZFS" id="BpL3OgQHR9" role="p0ZFv">
              <property role="p0ZFt" value="*" />
              <node concept="p0ZFS" id="BpL3OgQHRc" role="p0ZFs">
                <property role="p0ZFt" value="*" />
                <node concept="p0ZFP" id="BpL3OgQHRf" role="p0ZFs">
                  <property role="p0ZFq" value="x" />
                  <node concept="p0ZFY" id="BpL3OgQHRh" role="p0ZFl">
                    <ref role="peofi" node="BpL3OgQHQY" />
                  </node>
                </node>
                <node concept="p0ZFP" id="BpL3OgQHRi" role="p0ZFv">
                  <property role="p0ZFq" value="y" />
                  <node concept="p0ZFY" id="BpL3OgQHRk" role="p0ZFl">
                    <ref role="peofi" node="BpL3OgQHQY" />
                  </node>
                </node>
              </node>
              <node concept="p0ZFP" id="BpL3OgQHRl" role="p0ZFv">
                <property role="p0ZFq" value="z" />
                <node concept="p0ZFY" id="BpL3OgQHRn" role="p0ZFl">
                  <ref role="peofi" node="BpL3OgQHQY" />
                </node>
              </node>
            </node>
          </node>
          <node concept="p0ZFQ" id="BpL3OgQHRo" role="p0ZFb">
            <node concept="p0ZFG" id="BpL3OgQHRp" role="p0ZFj" />
          </node>
        </node>
        <node concept="p0ZFM" id="BpL3OgQHRq" role="p0ZFj">
          <node concept="p0ZFV" id="BpL3OgQHRt" role="p0ZF8">
            <property role="p0ZFu" value="!" />
            <node concept="p0ZFU" id="BpL3OgQHRv" role="p0ZFp">
              <node concept="p0ZFY" id="BpL3OgQHRy" role="p0ZFo">
                <ref role="peofi" node="BpL3OgQHQV" />
              </node>
              <node concept="pevns" id="BpL3OgWCC$" role="p0ZFr" />
            </node>
          </node>
          <node concept="p0ZFQ" id="BpL3OgQHR$" role="p0ZFb">
            <node concept="p0ZFK" id="BpL3OgQHR_" role="p0ZFj">
              <node concept="p0ZFU" id="BpL3OgQHRC" role="p0ZFf">
                <node concept="p0ZFY" id="BpL3OgQHRF" role="p0ZFo">
                  <ref role="peofi" node="BpL3OgQHQZ" />
                </node>
                <node concept="pevns" id="BpL3OgWCC_" role="p0ZFr" />
              </node>
              <node concept="p0ZFT" id="BpL3OgQHRH" role="p0ZFe">
                <property role="p0ZFy" value="0" />
              </node>
            </node>
            <node concept="p0ZFG" id="BpL3OgQHRI" role="p0ZFj" />
          </node>
        </node>
        <node concept="p0ZFL" id="BpL3OgQHRJ" role="p0ZFj">
          <property role="TrG5h" value="r" />
          <node concept="p0ZFO" id="BpL3OgQHRL" role="p0ZFd">
            <ref role="peofe" node="BpL3OgQHLM" />
            <node concept="p0ZFY" id="BpL3OgQHRM" role="p0ZFn">
              <ref role="peofi" node="BpL3OgQHQW" />
            </node>
            <node concept="pevns" id="BpL3OgWCCA" role="p0ZFn" />
          </node>
          <node concept="p1rTj" id="BpL3OgRsgy" role="p1kwX" />
        </node>
        <node concept="p0ZFK" id="BpL3OgQHRO" role="p0ZFj">
          <node concept="p0ZFU" id="BpL3OgQHRR" role="p0ZFf">
            <node concept="p0ZFY" id="BpL3OgQHRU" role="p0ZFo">
              <ref role="peofi" node="BpL3OgQHQZ" />
            </node>
            <node concept="pevns" id="BpL3OgWCCB" role="p0ZFr" />
          </node>
          <node concept="p0ZFS" id="BpL3OgQHRW" role="p0ZFe">
            <property role="p0ZFt" value="+" />
            <node concept="p0ZFS" id="BpL3OgQHRZ" role="p0ZFs">
              <property role="p0ZFt" value="%" />
              <node concept="p0ZFY" id="BpL3OgQHS2" role="p0ZFs">
                <ref role="peofi" node="BpL3OgQHRJ" />
              </node>
              <node concept="p0ZFS" id="BpL3OgQHS3" role="p0ZFv">
                <property role="p0ZFt" value="*" />
                <node concept="p0ZFP" id="BpL3OgQHS6" role="p0ZFs">
                  <property role="p0ZFq" value="x" />
                  <node concept="p0ZFY" id="BpL3OgQHS8" role="p0ZFl">
                    <ref role="peofi" node="BpL3OgQHQY" />
                  </node>
                </node>
                <node concept="p0ZFP" id="BpL3OgQHS9" role="p0ZFv">
                  <property role="p0ZFq" value="y" />
                  <node concept="p0ZFY" id="BpL3OgQHSb" role="p0ZFl">
                    <ref role="peofi" node="BpL3OgQHQY" />
                  </node>
                </node>
              </node>
            </node>
            <node concept="p0ZFT" id="BpL3OgQHSc" role="p0ZFv">
              <property role="p0ZFy" value="1" />
            </node>
          </node>
        </node>
        <node concept="p0ZFN" id="BpL3OgQHSd" role="p0ZFj">
          <node concept="p1rT8" id="BpL3OgRs5r" role="p0ZF9">
            <node concept="p0ZFS" id="BpL3OgRs5u" role="p1rT7">
              <property role="p0ZFt" value="+" />
              <node concept="p0ZFY" id="BpL3OgRs5x" role="p0ZFs">
                <ref role="peofi" node="BpL3OgQHQX" />
              </node>
              <node concept="p0ZFY" id="BpL3OgRs5y" role="p0ZFv">
                <ref role="peofi" node="BpL3OgQHRJ" />
              </node>
            </node>
            <node concept="p0ZFT" id="BpL3OgRs5z" role="p1rT6">
              <property role="p0ZFy" value="1u" />
            </node>
          </node>
        </node>
      </node>
      <node concept="p1rTg" id="BpL3OgRsgs" role="p1kZ5" />
      <node concept="pevnv" id="BpL3OgWCCy" role="peofa" />
    </node>
    <node concept="p0ZE2" id="BpL3OgQHSn" role="p0ZFJ">
      <property role="TrG5h" value="areas" />
      <property role="p0ZFD" value="true" />
      <node concept="p0ZFX" id="BpL3OgQHSp" role="p0ZFF">
        <property role="TrG5h" value="mask" />
        <property role="p0ZFx" value="true" />
        <property role="p0ZFB" value="0" />
        <property role="p0ZFA" value="false" />
        <node concept="p1rTi" id="BpL3OgRsg$" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3OgQHSq" role="p0ZFF">
        <property role="TrG5h" value="parent" />
        <property role="p0ZFx" value="true" />
        <property role="p0ZFB" value="1" />
        <property role="p0ZFA" value="false" />
        <node concept="p1rTd" id="BpL3OgRsg_" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3OgQHSr" role="p0ZFF">
        <property role="TrG5h" value="counts" />
        <property role="p0ZFx" value="true" />
        <property role="p0ZFB" value="2" />
        <property role="p0ZFA" value="false" />
        <node concept="p1rTd" id="BpL3OgRsgA" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3OgQHSs" role="p0ZFF">
        <property role="TrG5h" value="shape" />
        <property role="p0ZFx" value="true" />
        <property role="p0ZFB" value="3" />
        <property role="p0ZFA" value="false" />
        <node concept="p1rTf" id="BpL3OgRsgB" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3OgQHSt" role="p0ZFF">
        <property role="TrG5h" value="labels" />
        <property role="p0ZFx" value="true" />
        <property role="p0ZFB" value="4" />
        <property role="p0ZFA" value="false" />
        <node concept="p1rTc" id="BpL3OgRsgC" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3OgQHSu" role="p0ZFF">
        <property role="TrG5h" value="output" />
        <property role="p0ZFx" value="true" />
        <property role="p0ZFB" value="5" />
        <property role="p0ZFA" value="false" />
        <node concept="p1rTc" id="BpL3OgRsgD" role="p1kZ1" />
      </node>
      <node concept="p0ZFQ" id="BpL3OgQHSw" role="p0ZFE">
        <node concept="p0ZFL" id="BpL3OgQHSx" role="p0ZFj">
          <property role="TrG5h" value="plane" />
          <node concept="p0ZFS" id="BpL3OgQHSz" role="p0ZFd">
            <property role="p0ZFt" value="*" />
            <node concept="p0ZFP" id="BpL3OgQHSA" role="p0ZFs">
              <property role="p0ZFq" value="x" />
              <node concept="p0ZFY" id="BpL3OgQHSC" role="p0ZFl">
                <ref role="peofi" node="BpL3OgQHSs" />
              </node>
            </node>
            <node concept="p0ZFP" id="BpL3OgQHSD" role="p0ZFv">
              <property role="p0ZFq" value="y" />
              <node concept="p0ZFY" id="BpL3OgQHSF" role="p0ZFl">
                <ref role="peofi" node="BpL3OgQHSs" />
              </node>
            </node>
          </node>
          <node concept="p1rTj" id="BpL3OgRsgE" role="p1kwX" />
        </node>
        <node concept="p0ZFM" id="BpL3OgQHSG" role="p0ZFj">
          <node concept="p0ZFS" id="BpL3OgQHSJ" role="p0ZF8">
            <property role="p0ZFt" value="&gt;=" />
            <node concept="pevns" id="BpL3OgWCCD" role="p0ZFs" />
            <node concept="p0ZFS" id="BpL3OgQHSN" role="p0ZFv">
              <property role="p0ZFt" value="*" />
              <node concept="p0ZFY" id="BpL3OgQHSQ" role="p0ZFs">
                <ref role="peofi" node="BpL3OgQHSx" />
              </node>
              <node concept="p0ZFP" id="BpL3OgQHSR" role="p0ZFv">
                <property role="p0ZFq" value="z" />
                <node concept="p0ZFY" id="BpL3OgQHST" role="p0ZFl">
                  <ref role="peofi" node="BpL3OgQHSs" />
                </node>
              </node>
            </node>
          </node>
          <node concept="p0ZFQ" id="BpL3OgQHSU" role="p0ZFb">
            <node concept="p0ZFG" id="BpL3OgQHSV" role="p0ZFj" />
          </node>
        </node>
        <node concept="p0ZFL" id="BpL3OgQHSW" role="p0ZFj">
          <property role="TrG5h" value="label" />
          <node concept="p0ZFU" id="BpL3OgQHSY" role="p0ZFd">
            <node concept="p0ZFY" id="BpL3OgQHT1" role="p0ZFo">
              <ref role="peofi" node="BpL3OgQHSt" />
            </node>
            <node concept="pevns" id="BpL3OgWCCE" role="p0ZFr" />
          </node>
          <node concept="p1rTj" id="BpL3OgRsgF" role="p1kwX" />
        </node>
        <node concept="p0ZFK" id="BpL3OgQHT3" role="p0ZFj">
          <node concept="p0ZFU" id="BpL3OgQHT6" role="p0ZFf">
            <node concept="p0ZFY" id="BpL3OgQHT9" role="p0ZFo">
              <ref role="peofi" node="BpL3OgQHSu" />
            </node>
            <node concept="pevns" id="BpL3OgWCCF" role="p0ZFr" />
          </node>
          <node concept="p0ZFR" id="BpL3OgQHTb" role="p0ZFe">
            <node concept="p0ZFY" id="BpL3OgQHTf" role="p0ZFm">
              <ref role="peofi" node="BpL3OgQHSW" />
            </node>
            <node concept="p1rTe" id="BpL3OgRs5$" role="p0ZFh">
              <node concept="p0ZFS" id="BpL3OgRs5A" role="p1rTa">
                <property role="p0ZFt" value="-" />
                <node concept="p0ZFS" id="BpL3OgRs5D" role="p0ZFs">
                  <property role="p0ZFt" value="+" />
                  <node concept="p0ZFS" id="BpL3OgRs5G" role="p0ZFs">
                    <property role="p0ZFt" value="+" />
                    <node concept="p0ZFY" id="BpL3OgRs5J" role="p0ZFs">
                      <ref role="peofi" node="BpL3OgQHSr" />
                    </node>
                    <node concept="p0ZFS" id="BpL3OgRs5K" role="p0ZFv">
                      <property role="p0ZFt" value="*" />
                      <node concept="p0ZFS" id="BpL3OgRs5N" role="p0ZFs">
                        <property role="p0ZFt" value="/" />
                        <node concept="pevns" id="BpL3OgWCCG" role="p0ZFs" />
                        <node concept="p0ZFY" id="BpL3OgRs5R" role="p0ZFv">
                          <ref role="peofi" node="BpL3OgQHSx" />
                        </node>
                      </node>
                      <node concept="p0ZFY" id="BpL3OgRs5S" role="p0ZFv">
                        <ref role="peofi" node="BpL3OgQHSx" />
                      </node>
                    </node>
                  </node>
                  <node concept="p0ZFY" id="BpL3OgRs5T" role="p0ZFv">
                    <ref role="peofi" node="BpL3OgQHSW" />
                  </node>
                </node>
                <node concept="p0ZFT" id="BpL3OgRs5U" role="p0ZFv">
                  <property role="p0ZFy" value="1" />
                </node>
              </node>
            </node>
            <node concept="p0ZFT" id="BpL3OgQHTB" role="p0ZFg">
              <property role="p0ZFy" value="0" />
            </node>
          </node>
        </node>
      </node>
      <node concept="p1rTg" id="BpL3OgRsgz" role="p1kZ5" />
      <node concept="pevnv" id="BpL3OgWCCC" role="peofa" />
    </node>
  </node>
  <node concept="pevno" id="BpL3OgWCCR">
    <property role="TrG5h" value="components_launch" />
    <ref role="pevni" node="BpL3OgQHKV" resolve="components" />
    <node concept="pevnr" id="BpL3OgWCCW" role="pevng">
      <property role="TrG5h" value="mask" />
      <property role="pevnc" value="input" />
      <ref role="paaHl" node="BpL3OgWCD5" />
      <node concept="p1rTi" id="BpL3OgWCCY" role="pevnf" />
    </node>
    <node concept="pevnr" id="BpL3OgWCCZ" role="pevng">
      <property role="TrG5h" value="parent" />
      <property role="pevnc" value="scratch" />
      <ref role="paaHl" node="BpL3OgWCD5" />
      <node concept="p1rTd" id="BpL3OgWCD1" role="pevnf" />
    </node>
    <node concept="pevnr" id="BpL3OgWCD2" role="pevng">
      <property role="TrG5h" value="counts" />
      <property role="pevnc" value="scratch" />
      <ref role="paaHl" node="BpL3OgWCD5" />
      <node concept="p1rTd" id="BpL3OgWCD4" role="pevnf" />
    </node>
    <node concept="pevnr" id="BpL3OgWCD5" role="pevng">
      <property role="TrG5h" value="shape" />
      <property role="pevnc" value="shape" />
      <node concept="p1rTf" id="BpL3OgWCD7" role="pevnf" />
    </node>
    <node concept="pevnr" id="BpL3OgWCD8" role="pevng">
      <property role="TrG5h" value="labels" />
      <property role="pevnc" value="output" />
      <ref role="paaHl" node="BpL3OgWCD5" />
      <node concept="p1rTc" id="BpL3OgWCDa" role="pevnf" />
    </node>
    <node concept="pevnr" id="BpL3OgWCDb" role="pevng">
      <property role="TrG5h" value="output" />
      <property role="pevnc" value="output" />
      <ref role="paaHl" node="BpL3OgWCD5" />
      <node concept="p1rTc" id="BpL3OgWCDd" role="pevnf" />
    </node>
    <node concept="pevnq" id="BpL3OgWCDe" role="pevnj">
      <property role="pevn9" value="128" />
      <property role="pevn8" value="true" />
      <ref role="pevna" node="BpL3OgQHKY" resolve="initialize" />
      <ref role="pevn5" node="BpL3OgWCD5" resolve="shape" />
      <node concept="pevnl" id="BpL3OgWCDg" role="pevnb">
        <ref role="pevn4" node="BpL3OgQHL0" resolve="mask" />
        <ref role="pevn7" node="BpL3OgWCCW" resolve="mask" />
      </node>
      <node concept="pevnl" id="BpL3OgWCDh" role="pevnb">
        <ref role="pevn4" node="BpL3OgQHL1" resolve="parent" />
        <ref role="pevn7" node="BpL3OgWCCZ" />
      </node>
      <node concept="pevnl" id="BpL3OgWCDi" role="pevnb">
        <ref role="pevn4" node="BpL3OgQHL2" resolve="counts" />
        <ref role="pevn7" node="BpL3OgWCD2" resolve="counts" />
      </node>
      <node concept="pevnl" id="BpL3OgWCDj" role="pevnb">
        <ref role="pevn4" node="BpL3OgQHL3" resolve="shape" />
        <ref role="pevn7" node="BpL3OgWCD5" resolve="shape" />
      </node>
    </node>
    <node concept="pevnq" id="BpL3OgWCDk" role="pevnj">
      <property role="pevn9" value="128" />
      <property role="pevn8" value="true" />
      <ref role="pevna" node="BpL3OgQHNA" resolve="merge" />
      <ref role="pevn5" node="BpL3OgWCD5" resolve="shape" />
      <node concept="pevnl" id="BpL3OgWCDm" role="pevnb">
        <ref role="pevn4" node="BpL3OgQHNC" resolve="mask" />
        <ref role="pevn7" node="BpL3OgWCCW" resolve="mask" />
      </node>
      <node concept="pevnl" id="BpL3OgWCDn" role="pevnb">
        <ref role="pevn4" node="BpL3OgQHND" resolve="parent" />
        <ref role="pevn7" node="BpL3OgWCCZ" />
      </node>
      <node concept="pevnl" id="BpL3OgWCDo" role="pevnb">
        <ref role="pevn4" node="BpL3OgQHNE" resolve="counts" />
        <ref role="pevn7" node="BpL3OgWCD2" resolve="counts" />
      </node>
      <node concept="pevnl" id="BpL3OgWCDp" role="pevnb">
        <ref role="pevn4" node="BpL3OgQHNF" resolve="shape" />
        <ref role="pevn7" node="BpL3OgWCD5" resolve="shape" />
      </node>
    </node>
    <node concept="pevnq" id="BpL3OgWCDq" role="pevnj">
      <property role="pevn9" value="128" />
      <property role="pevn8" value="true" />
      <ref role="pevna" node="BpL3OgQHQT" resolve="label_count" />
      <ref role="pevn5" node="BpL3OgWCD5" resolve="shape" />
      <node concept="pevnl" id="BpL3OgWCDs" role="pevnb">
        <ref role="pevn4" node="BpL3OgQHQV" resolve="mask" />
        <ref role="pevn7" node="BpL3OgWCCW" resolve="mask" />
      </node>
      <node concept="pevnl" id="BpL3OgWCDt" role="pevnb">
        <ref role="pevn4" node="BpL3OgQHQW" resolve="parent" />
        <ref role="pevn7" node="BpL3OgWCCZ" resolve="parent" />
      </node>
      <node concept="pevnl" id="BpL3OgWCDu" role="pevnb">
        <ref role="pevn4" node="BpL3OgQHQX" resolve="counts" />
        <ref role="pevn7" node="BpL3OgWCD2" resolve="counts" />
      </node>
      <node concept="pevnl" id="BpL3OgWCDv" role="pevnb">
        <ref role="pevn4" node="BpL3OgQHQY" resolve="shape" />
        <ref role="pevn7" node="BpL3OgWCD5" resolve="shape" />
      </node>
      <node concept="pevnl" id="BpL3OgWCDw" role="pevnb">
        <ref role="pevn4" node="BpL3OgQHQZ" resolve="labels" />
        <ref role="pevn7" node="BpL3OgWCD8" resolve="labels" />
      </node>
    </node>
    <node concept="pevnq" id="BpL3OgWCDx" role="pevnj">
      <property role="pevn9" value="128" />
      <property role="pevn8" value="true" />
      <ref role="pevna" node="BpL3OgQHSn" resolve="areas" />
      <ref role="pevn5" node="BpL3OgWCD5" resolve="shape" />
      <node concept="pevnl" id="BpL3OgWCDz" role="pevnb">
        <ref role="pevn4" node="BpL3OgQHSp" resolve="mask" />
        <ref role="pevn7" node="BpL3OgWCCW" resolve="mask" />
      </node>
      <node concept="pevnl" id="BpL3OgWCD$" role="pevnb">
        <ref role="pevn4" node="BpL3OgQHSq" resolve="parent" />
        <ref role="pevn7" node="BpL3OgWCCZ" resolve="parent" />
      </node>
      <node concept="pevnl" id="BpL3OgWCD_" role="pevnb">
        <ref role="pevn4" node="BpL3OgQHSr" resolve="counts" />
        <ref role="pevn7" node="BpL3OgWCD2" resolve="counts" />
      </node>
      <node concept="pevnl" id="BpL3OgWCDA" role="pevnb">
        <ref role="pevn4" node="BpL3OgQHSs" resolve="shape" />
        <ref role="pevn7" node="BpL3OgWCD5" resolve="shape" />
      </node>
      <node concept="pevnl" id="BpL3OgWCDB" role="pevnb">
        <ref role="pevn4" node="BpL3OgQHSt" resolve="labels" />
        <ref role="pevn7" node="BpL3OgWCD8" resolve="labels" />
      </node>
      <node concept="pevnl" id="BpL3OgWCDC" role="pevnb">
        <ref role="pevn4" node="BpL3OgQHSu" resolve="output" />
        <ref role="pevn7" node="BpL3OgWCDb" resolve="output" />
      </node>
    </node>
  </node>
  <node concept="p0ZE3" id="BpL3OgZjH1">
    <property role="TrG5h" value="threshold" />
    <node concept="p0ZE2" id="BpL3OgZjH5" role="p0ZFJ">
      <property role="TrG5h" value="apply_threshold" />
      <property role="p0ZFD" value="true" />
      <node concept="p0ZFX" id="BpL3OgZjH8" role="p0ZFF">
        <property role="TrG5h" value="intensity" />
        <property role="p0ZFB" value="0" />
        <property role="p0ZFx" value="true" />
        <node concept="p1rTi" id="BpL3OgZjHa" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3OgZjHb" role="p0ZFF">
        <property role="TrG5h" value="cutoff" />
        <property role="p0ZFB" value="1" />
        <property role="p0ZFx" value="true" />
        <node concept="p1rTi" id="BpL3OgZjHd" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3OgZjHe" role="p0ZFF">
        <property role="TrG5h" value="dimensions" />
        <property role="p0ZFB" value="2" />
        <property role="p0ZFx" value="true" />
        <node concept="p1rTf" id="BpL3OgZjHg" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3OgZjHh" role="p0ZFF">
        <property role="TrG5h" value="binary" />
        <property role="p0ZFB" value="3" />
        <property role="p0ZFx" value="true" />
        <node concept="p1rTc" id="BpL3OgZjHj" role="p1kZ1" />
      </node>
      <node concept="p1rTg" id="BpL3OgZjHk" role="p1kZ5" />
      <node concept="pevnv" id="BpL3OgZjHl" role="peofa" />
      <node concept="p0ZFQ" id="BpL3OgZjHn" role="p0ZFE">
        <node concept="p0ZFM" id="BpL3OgZjHo" role="p0ZFj">
          <node concept="p0ZFS" id="BpL3OgZjHr" role="p0ZF8">
            <property role="p0ZFt" value="&gt;=" />
            <node concept="pevns" id="BpL3OgZjHu" role="p0ZFs" />
            <node concept="p0ZFS" id="BpL3OgZjHv" role="p0ZFv">
              <property role="p0ZFt" value="*" />
              <node concept="p0ZFS" id="BpL3OgZjHy" role="p0ZFs">
                <property role="p0ZFt" value="*" />
                <node concept="p0ZFP" id="BpL3OgZjH_" role="p0ZFs">
                  <property role="p0ZFq" value="x" />
                  <node concept="p0ZFY" id="BpL3OgZjHB" role="p0ZFl">
                    <ref role="peofi" node="BpL3OgZjHe" resolve="dimensions" />
                  </node>
                </node>
                <node concept="p0ZFP" id="BpL3OgZjHC" role="p0ZFv">
                  <property role="p0ZFq" value="y" />
                  <node concept="p0ZFY" id="BpL3OgZjHE" role="p0ZFl">
                    <ref role="peofi" node="BpL3OgZjHe" resolve="dimensions" />
                  </node>
                </node>
              </node>
              <node concept="p0ZFP" id="BpL3OgZjHF" role="p0ZFv">
                <property role="p0ZFq" value="z" />
                <node concept="p0ZFY" id="BpL3OgZjHH" role="p0ZFl">
                  <ref role="peofi" node="BpL3OgZjHe" resolve="dimensions" />
                </node>
              </node>
            </node>
          </node>
          <node concept="p0ZFQ" id="BpL3OgZjHI" role="p0ZFb">
            <node concept="p0ZFG" id="BpL3OgZjHJ" role="p0ZFj" />
          </node>
        </node>
        <node concept="p0ZFK" id="BpL3OgZjHK" role="p0ZFj">
          <node concept="p0ZFU" id="BpL3OgZjHN" role="p0ZFf">
            <node concept="p0ZFY" id="BpL3OgZjHQ" role="p0ZFo">
              <ref role="peofi" node="BpL3OgZjHh" resolve="binary" />
            </node>
            <node concept="pevns" id="BpL3OgZjHR" role="p0ZFr" />
          </node>
          <node concept="p0ZFR" id="BpL3OgZjHS" role="p0ZFe">
            <node concept="p0ZFS" id="BpL3OgZjHW" role="p0ZFm">
              <property role="p0ZFt" value="&gt;=" />
              <node concept="p0ZFU" id="BpL3OgZjHZ" role="p0ZFs">
                <node concept="p0ZFY" id="BpL3OgZjI2" role="p0ZFo">
                  <ref role="peofi" node="BpL3OgZjH8" resolve="intensity" />
                </node>
                <node concept="pevns" id="BpL3OgZjI3" role="p0ZFr" />
              </node>
              <node concept="p0ZFU" id="BpL3OgZjI4" role="p0ZFv">
                <node concept="p0ZFY" id="BpL3OgZjI7" role="p0ZFo">
                  <ref role="peofi" node="BpL3OgZjHb" resolve="cutoff" />
                </node>
                <node concept="pevns" id="BpL3OgZjI8" role="p0ZFr" />
              </node>
            </node>
            <node concept="p0ZFT" id="BpL3OgZjI9" role="p0ZFh">
              <property role="p0ZFy" value="1" />
            </node>
            <node concept="p0ZFT" id="BpL3OgZjIa" role="p0ZFg">
              <property role="p0ZFy" value="0" />
            </node>
          </node>
        </node>
      </node>
    </node>
  </node>
  <node concept="pevno" id="BpL3OgZjIb">
    <property role="TrG5h" value="threshold_launch" />
    <ref role="pevni" node="BpL3OgZjH1" resolve="threshold" />
    <node concept="pevnr" id="BpL3OgZjIg" role="pevng">
      <property role="TrG5h" value="intensity" />
      <property role="pevnc" value="input" />
      <ref role="paaHl" node="BpL3OgZjIm" />
      <node concept="p1rTi" id="BpL3OgZjIi" role="pevnf" />
    </node>
    <node concept="pevnr" id="BpL3OgZjIj" role="pevng">
      <property role="TrG5h" value="cutoff" />
      <property role="pevnc" value="input" />
      <ref role="paaHl" node="BpL3OgZjIm" />
      <node concept="p1rTi" id="BpL3OgZjIl" role="pevnf" />
    </node>
    <node concept="pevnr" id="BpL3OgZjIm" role="pevng">
      <property role="TrG5h" value="dimensions" />
      <property role="pevnc" value="shape" />
      <node concept="p1rTf" id="BpL3OgZjIo" role="pevnf" />
    </node>
    <node concept="pevnr" id="BpL3OgZjIp" role="pevng">
      <property role="TrG5h" value="binary" />
      <property role="pevnc" value="output" />
      <ref role="paaHl" node="BpL3OgZjIm" />
      <node concept="p1rTc" id="BpL3OgZjIr" role="pevnf" />
    </node>
    <node concept="pevnq" id="BpL3OgZjIs" role="pevnj">
      <property role="pevn9" value="64" />
      <property role="pevn8" value="true" />
      <ref role="pevna" node="BpL3OgZjH5" resolve="apply_threshold" />
      <ref role="pevn5" node="BpL3OgZjIm" resolve="dimensions" />
      <node concept="pevnl" id="BpL3OgZjIu" role="pevnb">
        <ref role="pevn4" node="BpL3OgZjH8" resolve="intensity" />
        <ref role="pevn7" node="BpL3OgZjIg" resolve="intensity" />
      </node>
      <node concept="pevnl" id="BpL3OgZjIv" role="pevnb">
        <ref role="pevn4" node="BpL3OgZjHb" resolve="cutoff" />
        <ref role="pevn7" node="BpL3OgZjIj" resolve="cutoff" />
      </node>
      <node concept="pevnl" id="BpL3OgZjIw" role="pevnb">
        <ref role="pevn4" node="BpL3OgZjHe" resolve="dimensions" />
        <ref role="pevn7" node="BpL3OgZjIm" resolve="dimensions" />
      </node>
      <node concept="pevnl" id="BpL3OgZjIx" role="pevnb">
        <ref role="pevn4" node="BpL3OgZjHh" resolve="binary" />
        <ref role="pevn7" node="BpL3OgZjIp" resolve="binary" />
      </node>
    </node>
  </node>
  <node concept="p0ZE3" id="BpL3OgZkxa">
    <property role="TrG5h" value="cuda_threshold" />
    <node concept="p0ZE2" id="BpL3OgZkxe" role="p0ZFJ">
      <property role="TrG5h" value="apply_threshold" />
      <property role="p0ZFD" value="true" />
      <node concept="p0ZFX" id="BpL3OgZkxh" role="p0ZFF">
        <property role="TrG5h" value="intensity" />
        <property role="p0ZFB" value="0" />
        <property role="p0ZFx" value="true" />
        <node concept="p1rTi" id="BpL3OgZkxj" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3OgZkxk" role="p0ZFF">
        <property role="TrG5h" value="cutoff" />
        <property role="p0ZFB" value="1" />
        <property role="p0ZFx" value="true" />
        <node concept="p1rTi" id="BpL3OgZkxm" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3OgZkxn" role="p0ZFF">
        <property role="TrG5h" value="dimensions" />
        <property role="p0ZFB" value="2" />
        <property role="p0ZFx" value="true" />
        <node concept="p1rTf" id="BpL3OgZkxp" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3OgZkxq" role="p0ZFF">
        <property role="TrG5h" value="binary" />
        <property role="p0ZFB" value="3" />
        <property role="p0ZFx" value="true" />
        <node concept="p1rTc" id="BpL3OgZkxs" role="p1kZ1" />
      </node>
      <node concept="p1rTg" id="BpL3OgZkxt" role="p1kZ5" />
      <node concept="pevnv" id="BpL3OgZkxu" role="peofa" />
      <node concept="p0ZFQ" id="BpL3OgZkxw" role="p0ZFE">
        <node concept="p0ZFM" id="BpL3OgZkxx" role="p0ZFj">
          <node concept="p0ZFS" id="BpL3OgZkx$" role="p0ZF8">
            <property role="p0ZFt" value="&gt;=" />
            <node concept="pevns" id="BpL3OgZkxB" role="p0ZFs" />
            <node concept="p0ZFS" id="BpL3OgZkxC" role="p0ZFv">
              <property role="p0ZFt" value="*" />
              <node concept="p0ZFS" id="BpL3OgZkxF" role="p0ZFs">
                <property role="p0ZFt" value="*" />
                <node concept="p0ZFP" id="BpL3OgZkxI" role="p0ZFs">
                  <property role="p0ZFq" value="x" />
                  <node concept="p0ZFY" id="BpL3OgZkxK" role="p0ZFl">
                    <ref role="peofi" node="BpL3OgZkxn" resolve="dimensions" />
                  </node>
                </node>
                <node concept="p0ZFP" id="BpL3OgZkxL" role="p0ZFv">
                  <property role="p0ZFq" value="y" />
                  <node concept="p0ZFY" id="BpL3OgZkxN" role="p0ZFl">
                    <ref role="peofi" node="BpL3OgZkxn" resolve="dimensions" />
                  </node>
                </node>
              </node>
              <node concept="p0ZFP" id="BpL3OgZkxO" role="p0ZFv">
                <property role="p0ZFq" value="z" />
                <node concept="p0ZFY" id="BpL3OgZkxQ" role="p0ZFl">
                  <ref role="peofi" node="BpL3OgZkxn" resolve="dimensions" />
                </node>
              </node>
            </node>
          </node>
          <node concept="p0ZFQ" id="BpL3OgZkxR" role="p0ZFb">
            <node concept="p0ZFG" id="BpL3OgZkxS" role="p0ZFj" />
          </node>
        </node>
        <node concept="p0ZFK" id="BpL3OgZkxT" role="p0ZFj">
          <node concept="p0ZFU" id="BpL3OgZkxW" role="p0ZFf">
            <node concept="p0ZFY" id="BpL3OgZkxZ" role="p0ZFo">
              <ref role="peofi" node="BpL3OgZkxq" resolve="binary" />
            </node>
            <node concept="pevns" id="BpL3OgZky0" role="p0ZFr" />
          </node>
          <node concept="p0ZFR" id="BpL3OgZky1" role="p0ZFe">
            <node concept="p0ZFS" id="BpL3OgZky5" role="p0ZFm">
              <property role="p0ZFt" value="&gt;=" />
              <node concept="p0ZFU" id="BpL3OgZky8" role="p0ZFs">
                <node concept="p0ZFY" id="BpL3OgZkyb" role="p0ZFo">
                  <ref role="peofi" node="BpL3OgZkxh" resolve="intensity" />
                </node>
                <node concept="pevns" id="BpL3OgZkyc" role="p0ZFr" />
              </node>
              <node concept="p0ZFU" id="BpL3OgZkyd" role="p0ZFv">
                <node concept="p0ZFY" id="BpL3OgZkyg" role="p0ZFo">
                  <ref role="peofi" node="BpL3OgZkxk" resolve="cutoff" />
                </node>
                <node concept="pevns" id="BpL3OgZkyh" role="p0ZFr" />
              </node>
            </node>
            <node concept="p0ZFT" id="BpL3OgZkyi" role="p0ZFh">
              <property role="p0ZFy" value="1" />
            </node>
            <node concept="p0ZFT" id="BpL3OgZkyj" role="p0ZFg">
              <property role="p0ZFy" value="0" />
            </node>
          </node>
        </node>
      </node>
    </node>
  </node>
  <node concept="pevno" id="BpL3OgZkyk">
    <property role="TrG5h" value="cuda_threshold_launch" />
    <ref role="pevni" node="BpL3OgZkxa" resolve="cuda_threshold" />
    <node concept="pevnr" id="BpL3OgZkyp" role="pevng">
      <property role="TrG5h" value="intensity" />
      <property role="pevnc" value="input" />
      <ref role="paaHl" node="BpL3OgZkyv" />
      <node concept="p1rTi" id="BpL3OgZkyr" role="pevnf" />
    </node>
    <node concept="pevnr" id="BpL3OgZkys" role="pevng">
      <property role="TrG5h" value="cutoff" />
      <property role="pevnc" value="input" />
      <ref role="paaHl" node="BpL3OgZkyv" />
      <node concept="p1rTi" id="BpL3OgZkyu" role="pevnf" />
    </node>
    <node concept="pevnr" id="BpL3OgZkyv" role="pevng">
      <property role="TrG5h" value="dimensions" />
      <property role="pevnc" value="shape" />
      <node concept="p1rTf" id="BpL3OgZkyx" role="pevnf" />
    </node>
    <node concept="pevnr" id="BpL3OgZkyy" role="pevng">
      <property role="TrG5h" value="binary" />
      <property role="pevnc" value="output" />
      <ref role="paaHl" node="BpL3OgZkyv" />
      <node concept="p1rTc" id="BpL3OgZky$" role="pevnf" />
    </node>
    <node concept="pevnq" id="BpL3OgZky_" role="pevnj">
      <property role="pevn9" value="64" />
      <property role="pevn8" value="true" />
      <ref role="pevna" node="BpL3OgZkxe" resolve="apply_threshold" />
      <ref role="pevn5" node="BpL3OgZkyv" resolve="dimensions" />
      <node concept="pevnl" id="BpL3OgZkyB" role="pevnb">
        <ref role="pevn4" node="BpL3OgZkxh" resolve="intensity" />
        <ref role="pevn7" node="BpL3OgZkyp" resolve="intensity" />
      </node>
      <node concept="pevnl" id="BpL3OgZkyC" role="pevnb">
        <ref role="pevn4" node="BpL3OgZkxk" resolve="cutoff" />
        <ref role="pevn7" node="BpL3OgZkys" resolve="cutoff" />
      </node>
      <node concept="pevnl" id="BpL3OgZkyD" role="pevnb">
        <ref role="pevn4" node="BpL3OgZkxn" resolve="dimensions" />
        <ref role="pevn7" node="BpL3OgZkyv" resolve="dimensions" />
      </node>
      <node concept="pevnl" id="BpL3OgZkyE" role="pevnb">
        <ref role="pevn4" node="BpL3OgZkxq" resolve="binary" />
        <ref role="pevn7" node="BpL3OgZkyy" resolve="binary" />
      </node>
    </node>
  </node>
  <node concept="p0ZE3" id="BpL3OgZlnP">
    <property role="TrG5h" value="cuda_classify" />
    <node concept="p0ZE2" id="BpL3OgZlnT" role="p0ZFJ">
      <property role="TrG5h" value="classify_bytes" />
      <property role="p0ZFD" value="true" />
      <node concept="p0ZFX" id="BpL3OgZlnW" role="p0ZFF">
        <property role="TrG5h" value="classes" />
        <property role="p0ZFB" value="0" />
        <property role="p0ZFx" value="true" />
        <node concept="p1rTc" id="BpL3OgZlnY" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3OgZlnZ" role="p0ZFF">
        <property role="TrG5h" value="extent" />
        <property role="p0ZFB" value="1" />
        <property role="p0ZFx" value="true" />
        <node concept="p1rTf" id="BpL3OgZlo1" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3OgZlo2" role="p0ZFF">
        <property role="TrG5h" value="limits" />
        <property role="p0ZFB" value="2" />
        <property role="p0ZFx" value="true" />
        <node concept="p1rTi" id="BpL3OgZlo4" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3OgZlo5" role="p0ZFF">
        <property role="TrG5h" value="pixels" />
        <property role="p0ZFB" value="3" />
        <property role="p0ZFx" value="true" />
        <node concept="p1rTi" id="BpL3OgZlo7" role="p1kZ1" />
      </node>
      <node concept="p1rTg" id="BpL3OgZlo8" role="p1kZ5" />
      <node concept="pevnv" id="BpL3OgZlo9" role="peofa" />
      <node concept="p0ZFQ" id="BpL3OgZlob" role="p0ZFE">
        <node concept="p0ZFM" id="BpL3OgZloc" role="p0ZFj">
          <node concept="p0ZFS" id="BpL3OgZlof" role="p0ZF8">
            <property role="p0ZFt" value="&gt;=" />
            <node concept="pevns" id="BpL3OgZloi" role="p0ZFs" />
            <node concept="p0ZFS" id="BpL3OgZloj" role="p0ZFv">
              <property role="p0ZFt" value="*" />
              <node concept="p0ZFP" id="BpL3OgZlom" role="p0ZFs">
                <property role="p0ZFq" value="x" />
                <node concept="p0ZFY" id="BpL3OgZloo" role="p0ZFl">
                  <ref role="peofi" node="BpL3OgZlnZ" resolve="extent" />
                </node>
              </node>
              <node concept="p0ZFS" id="BpL3OgZlop" role="p0ZFv">
                <property role="p0ZFt" value="*" />
                <node concept="p0ZFP" id="BpL3OgZlos" role="p0ZFs">
                  <property role="p0ZFq" value="y" />
                  <node concept="p0ZFY" id="BpL3OgZlou" role="p0ZFl">
                    <ref role="peofi" node="BpL3OgZlnZ" resolve="extent" />
                  </node>
                </node>
                <node concept="p0ZFP" id="BpL3OgZlov" role="p0ZFv">
                  <property role="p0ZFq" value="z" />
                  <node concept="p0ZFY" id="BpL3OgZlox" role="p0ZFl">
                    <ref role="peofi" node="BpL3OgZlnZ" resolve="extent" />
                  </node>
                </node>
              </node>
            </node>
          </node>
          <node concept="p0ZFQ" id="BpL3OgZloy" role="p0ZFb">
            <node concept="p0ZFG" id="BpL3OgZloz" role="p0ZFj" />
          </node>
        </node>
        <node concept="p0ZFK" id="BpL3OgZlo$" role="p0ZFj">
          <node concept="p0ZFU" id="BpL3OgZloB" role="p0ZFf">
            <node concept="p0ZFY" id="BpL3OgZloE" role="p0ZFo">
              <ref role="peofi" node="BpL3OgZlnW" resolve="classes" />
            </node>
            <node concept="pevns" id="BpL3OgZloF" role="p0ZFr" />
          </node>
          <node concept="p0ZFR" id="BpL3OgZloG" role="p0ZFe">
            <node concept="p0ZFS" id="BpL3OgZloK" role="p0ZFm">
              <property role="p0ZFt" value="&lt;" />
              <node concept="p0ZFU" id="BpL3OgZloN" role="p0ZFs">
                <node concept="p0ZFY" id="BpL3OgZloQ" role="p0ZFo">
                  <ref role="peofi" node="BpL3OgZlo5" resolve="pixels" />
                </node>
                <node concept="pevns" id="BpL3OgZloR" role="p0ZFr" />
              </node>
              <node concept="p0ZFU" id="BpL3OgZloS" role="p0ZFv">
                <node concept="p0ZFY" id="BpL3OgZloV" role="p0ZFo">
                  <ref role="peofi" node="BpL3OgZlo2" resolve="limits" />
                </node>
                <node concept="pevns" id="BpL3OgZloW" role="p0ZFr" />
              </node>
            </node>
            <node concept="p0ZFT" id="BpL3OgZloX" role="p0ZFh">
              <property role="p0ZFy" value="7" />
            </node>
            <node concept="p0ZFT" id="BpL3OgZloY" role="p0ZFg">
              <property role="p0ZFy" value="3" />
            </node>
          </node>
        </node>
      </node>
    </node>
  </node>
  <node concept="pevno" id="BpL3OgZloZ">
    <property role="TrG5h" value="cuda_classify_launch" />
    <ref role="pevni" node="BpL3OgZlnP" resolve="cuda_classify" />
    <node concept="pevnr" id="BpL3OgZlp4" role="pevng">
      <property role="TrG5h" value="classes" />
      <property role="pevnc" value="output" />
      <ref role="paaHl" node="BpL3OgZlp7" />
      <node concept="p1rTc" id="BpL3OgZlp6" role="pevnf" />
    </node>
    <node concept="pevnr" id="BpL3OgZlp7" role="pevng">
      <property role="TrG5h" value="extent" />
      <property role="pevnc" value="shape" />
      <node concept="p1rTf" id="BpL3OgZlp9" role="pevnf" />
    </node>
    <node concept="pevnr" id="BpL3OgZlpa" role="pevng">
      <property role="TrG5h" value="limits" />
      <property role="pevnc" value="input" />
      <ref role="paaHl" node="BpL3OgZlp7" />
      <node concept="p1rTi" id="BpL3OgZlpc" role="pevnf" />
    </node>
    <node concept="pevnr" id="BpL3OgZlpd" role="pevng">
      <property role="TrG5h" value="pixels" />
      <property role="pevnc" value="input" />
      <ref role="paaHl" node="BpL3OgZlp7" />
      <node concept="p1rTi" id="BpL3OgZlpf" role="pevnf" />
    </node>
    <node concept="pevnq" id="BpL3OgZlpg" role="pevnj">
      <property role="pevn9" value="32" />
      <property role="pevn8" value="true" />
      <ref role="pevna" node="BpL3OgZlnT" resolve="classify_bytes" />
      <ref role="pevn5" node="BpL3OgZlp7" resolve="extent" />
      <node concept="pevnl" id="BpL3OgZlpi" role="pevnb">
        <ref role="pevn4" node="BpL3OgZlnW" resolve="classes" />
        <ref role="pevn7" node="BpL3OgZlp4" resolve="classes" />
      </node>
      <node concept="pevnl" id="BpL3OgZlpj" role="pevnb">
        <ref role="pevn4" node="BpL3OgZlnZ" resolve="extent" />
        <ref role="pevn7" node="BpL3OgZlp7" resolve="extent" />
      </node>
      <node concept="pevnl" id="BpL3OgZlpk" role="pevnb">
        <ref role="pevn4" node="BpL3OgZlo2" resolve="limits" />
        <ref role="pevn7" node="BpL3OgZlpa" resolve="limits" />
      </node>
      <node concept="pevnl" id="BpL3OgZlpl" role="pevnb">
        <ref role="pevn4" node="BpL3OgZlo5" resolve="pixels" />
        <ref role="pevn7" node="BpL3OgZlpd" resolve="pixels" />
      </node>
    </node>
  </node>
  <node concept="p0ZE3" id="BpL3Oh0whg">
    <property role="TrG5h" value="cuda_masks" />
    <node concept="p0ZE2" id="BpL3Oh0whk" role="p0ZFJ">
      <property role="TrG5h" value="combine_masks" />
      <property role="p0ZFD" value="true" />
      <node concept="p0ZFX" id="BpL3Oh0whn" role="p0ZFF">
        <property role="TrG5h" value="left" />
        <property role="p0ZFB" value="0" />
        <property role="p0ZFx" value="true" />
        <node concept="p1rTi" id="BpL3Oh0whp" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3Oh0whq" role="p0ZFF">
        <property role="TrG5h" value="right" />
        <property role="p0ZFB" value="1" />
        <property role="p0ZFx" value="true" />
        <node concept="p1rTi" id="BpL3Oh0whs" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3Oh0wht" role="p0ZFF">
        <property role="TrG5h" value="extent" />
        <property role="p0ZFB" value="2" />
        <property role="p0ZFx" value="true" />
        <node concept="p1rTf" id="BpL3Oh0whv" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3Oh0whw" role="p0ZFF">
        <property role="TrG5h" value="result" />
        <property role="p0ZFB" value="3" />
        <property role="p0ZFx" value="true" />
        <node concept="p1rTc" id="BpL3Oh0why" role="p1kZ1" />
      </node>
      <node concept="p1rTg" id="BpL3Oh0whz" role="p1kZ5" />
      <node concept="pevnv" id="BpL3Oh0wh$" role="peofa" />
      <node concept="p0ZFQ" id="BpL3Oh0whA" role="p0ZFE">
        <node concept="p0ZFM" id="BpL3Oh0whB" role="p0ZFj">
          <node concept="p0ZFS" id="BpL3Oh0whE" role="p0ZF8">
            <property role="p0ZFt" value="&gt;=" />
            <node concept="pevns" id="BpL3Oh0whH" role="p0ZFs" />
            <node concept="p0ZFS" id="BpL3Oh0whI" role="p0ZFv">
              <property role="p0ZFt" value="*" />
              <node concept="p0ZFS" id="BpL3Oh0whL" role="p0ZFs">
                <property role="p0ZFt" value="*" />
                <node concept="p0ZFP" id="BpL3Oh0whO" role="p0ZFs">
                  <property role="p0ZFq" value="x" />
                  <node concept="p0ZFY" id="BpL3Oh0whQ" role="p0ZFl">
                    <ref role="peofi" node="BpL3Oh0wht" resolve="extent" />
                  </node>
                </node>
                <node concept="p0ZFP" id="BpL3Oh0whR" role="p0ZFv">
                  <property role="p0ZFq" value="y" />
                  <node concept="p0ZFY" id="BpL3Oh0whT" role="p0ZFl">
                    <ref role="peofi" node="BpL3Oh0wht" resolve="extent" />
                  </node>
                </node>
              </node>
              <node concept="p0ZFP" id="BpL3Oh0whU" role="p0ZFv">
                <property role="p0ZFq" value="z" />
                <node concept="p0ZFY" id="BpL3Oh0whW" role="p0ZFl">
                  <ref role="peofi" node="BpL3Oh0wht" resolve="extent" />
                </node>
              </node>
            </node>
          </node>
          <node concept="p0ZFQ" id="BpL3Oh0whX" role="p0ZFb">
            <node concept="p0ZFG" id="BpL3Oh0whY" role="p0ZFj" />
          </node>
        </node>
        <node concept="p0ZFK" id="BpL3Oh0whZ" role="p0ZFj">
          <node concept="p0ZFU" id="BpL3Oh0wi2" role="p0ZFf">
            <node concept="p0ZFY" id="BpL3Oh0wi5" role="p0ZFo">
              <ref role="peofi" node="BpL3Oh0whw" resolve="result" />
            </node>
            <node concept="pevns" id="BpL3Oh0wi6" role="p0ZFr" />
          </node>
          <node concept="p0ZFS" id="BpL3Oh0wi7" role="p0ZFe">
            <property role="p0ZFt" value="|" />
            <node concept="p0ZFS" id="BpL3Oh0wia" role="p0ZFs">
              <property role="p0ZFt" value="|" />
              <node concept="p0ZFU" id="BpL3Oh0wid" role="p0ZFs">
                <node concept="p0ZFY" id="BpL3Oh0wig" role="p0ZFo">
                  <ref role="peofi" node="BpL3Oh0whn" resolve="left" />
                </node>
                <node concept="pevns" id="BpL3Oh0wih" role="p0ZFr" />
              </node>
              <node concept="p0ZFS" id="BpL3Oh0wii" role="p0ZFv">
                <property role="p0ZFt" value="^" />
                <node concept="p0ZFS" id="BpL3Oh0wil" role="p0ZFs">
                  <property role="p0ZFt" value="&amp;" />
                  <node concept="p0ZFU" id="BpL3Oh0wio" role="p0ZFs">
                    <node concept="p0ZFY" id="BpL3Oh0wir" role="p0ZFo">
                      <ref role="peofi" node="BpL3Oh0whq" resolve="right" />
                    </node>
                    <node concept="pevns" id="BpL3Oh0wis" role="p0ZFr" />
                  </node>
                  <node concept="p0ZFT" id="BpL3Oh0wit" role="p0ZFv">
                    <property role="p0ZFy" value="170" />
                  </node>
                </node>
                <node concept="p0ZFT" id="BpL3Oh0wiu" role="p0ZFv">
                  <property role="p0ZFy" value="85" />
                </node>
              </node>
            </node>
            <node concept="p0ZFT" id="BpL3Oh0wiv" role="p0ZFv">
              <property role="p0ZFy" value="2147483648u" />
            </node>
          </node>
        </node>
      </node>
    </node>
  </node>
  <node concept="pevno" id="BpL3Oh0wiw">
    <property role="TrG5h" value="cuda_masks_launch" />
    <ref role="pevni" node="BpL3Oh0whg" resolve="cuda_masks" />
    <node concept="pevnr" id="BpL3Oh0wi_" role="pevng">
      <property role="TrG5h" value="left" />
      <property role="pevnc" value="input" />
      <ref role="paaHl" node="BpL3Oh0wiF" />
      <node concept="p1rTi" id="BpL3Oh0wiB" role="pevnf" />
    </node>
    <node concept="pevnr" id="BpL3Oh0wiC" role="pevng">
      <property role="TrG5h" value="right" />
      <property role="pevnc" value="input" />
      <ref role="paaHl" node="BpL3Oh0wiF" />
      <node concept="p1rTi" id="BpL3Oh0wiE" role="pevnf" />
    </node>
    <node concept="pevnr" id="BpL3Oh0wiF" role="pevng">
      <property role="TrG5h" value="extent" />
      <property role="pevnc" value="shape" />
      <node concept="p1rTf" id="BpL3Oh0wiH" role="pevnf" />
    </node>
    <node concept="pevnr" id="BpL3Oh0wiI" role="pevng">
      <property role="TrG5h" value="result" />
      <property role="pevnc" value="output" />
      <ref role="paaHl" node="BpL3Oh0wiF" />
      <node concept="p1rTc" id="BpL3Oh0wiK" role="pevnf" />
    </node>
    <node concept="pevnq" id="BpL3Oh0wiL" role="pevnj">
      <property role="pevn9" value="128" />
      <property role="pevn8" value="true" />
      <ref role="pevna" node="BpL3Oh0whk" resolve="combine_masks" />
      <ref role="pevn5" node="BpL3Oh0wiF" resolve="extent" />
      <node concept="pevnl" id="BpL3Oh0wiN" role="pevnb">
        <ref role="pevn4" node="BpL3Oh0whn" resolve="left" />
        <ref role="pevn7" node="BpL3Oh0wi_" resolve="left" />
      </node>
      <node concept="pevnl" id="BpL3Oh0wiO" role="pevnb">
        <ref role="pevn4" node="BpL3Oh0whq" resolve="right" />
        <ref role="pevn7" node="BpL3Oh0wiC" resolve="right" />
      </node>
      <node concept="pevnl" id="BpL3Oh0wiP" role="pevnb">
        <ref role="pevn4" node="BpL3Oh0wht" resolve="extent" />
        <ref role="pevn7" node="BpL3Oh0wiF" resolve="extent" />
      </node>
      <node concept="pevnl" id="BpL3Oh0wiQ" role="pevnb">
        <ref role="pevn4" node="BpL3Oh0whw" resolve="result" />
        <ref role="pevn7" node="BpL3Oh0wiI" resolve="result" />
      </node>
    </node>
  </node>
  <node concept="p0ZE3" id="BpL3Oh1$S9">
    <property role="TrG5h" value="cuda_masked_copy" />
    <node concept="p0ZE2" id="BpL3Oh1$Sd" role="p0ZFJ">
      <property role="TrG5h" value="masked_copy" />
      <property role="p0ZFD" value="true" />
      <node concept="p0ZFX" id="BpL3Oh1$Sg" role="p0ZFF">
        <property role="TrG5h" value="pixels" />
        <property role="p0ZFB" value="0" />
        <property role="p0ZFx" value="true" />
        <node concept="p1rTi" id="BpL3Oh1$Si" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3Oh1$Sj" role="p0ZFF">
        <property role="TrG5h" value="mask" />
        <property role="p0ZFB" value="1" />
        <property role="p0ZFx" value="true" />
        <node concept="p1rTi" id="BpL3Oh1$Sl" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3Oh1$Sm" role="p0ZFF">
        <property role="TrG5h" value="output" />
        <property role="p0ZFB" value="2" />
        <property role="p0ZFx" value="true" />
        <node concept="p1rTc" id="BpL3Oh1$So" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3Oh1$Sp" role="p0ZFF">
        <property role="TrG5h" value="count" />
        <property role="p0ZFB" value="3" />
        <property role="p0ZFx" value="true" />
        <node concept="p1rTf" id="BpL3Oh1$Sr" role="p1kZ1" />
      </node>
      <node concept="p1rTg" id="BpL3Oh1$Ss" role="p1kZ5" />
      <node concept="pevnv" id="BpL3Oh1$St" role="peofa" />
      <node concept="p0ZFQ" id="BpL3Oh1$Sv" role="p0ZFE">
        <node concept="p0ZFM" id="BpL3Oh1$Sw" role="p0ZFj">
          <node concept="p0ZFS" id="BpL3Oh1$Sz" role="p0ZF8">
            <property role="p0ZFt" value="&gt;=" />
            <node concept="pevns" id="BpL3Oh1$SA" role="p0ZFs" />
            <node concept="p0ZFS" id="BpL3Oh1$SB" role="p0ZFv">
              <property role="p0ZFt" value="*" />
              <node concept="p0ZFS" id="BpL3Oh1$SE" role="p0ZFs">
                <property role="p0ZFt" value="*" />
                <node concept="p0ZFP" id="BpL3Oh1$SH" role="p0ZFs">
                  <property role="p0ZFq" value="x" />
                  <node concept="p0ZFY" id="BpL3Oh1$SJ" role="p0ZFl">
                    <ref role="peofi" node="BpL3Oh1$Sp" resolve="count" />
                  </node>
                </node>
                <node concept="p0ZFP" id="BpL3Oh1$SK" role="p0ZFv">
                  <property role="p0ZFq" value="y" />
                  <node concept="p0ZFY" id="BpL3Oh1$SM" role="p0ZFl">
                    <ref role="peofi" node="BpL3Oh1$Sp" resolve="count" />
                  </node>
                </node>
              </node>
              <node concept="p0ZFP" id="BpL3Oh1$SN" role="p0ZFv">
                <property role="p0ZFq" value="z" />
                <node concept="p0ZFY" id="BpL3Oh1$SP" role="p0ZFl">
                  <ref role="peofi" node="BpL3Oh1$Sp" resolve="count" />
                </node>
              </node>
            </node>
          </node>
          <node concept="p0ZFQ" id="BpL3Oh1$SQ" role="p0ZFb">
            <node concept="p0ZFG" id="BpL3Oh1$SR" role="p0ZFj" />
          </node>
        </node>
        <node concept="p0ZFK" id="BpL3Oh1$SS" role="p0ZFj">
          <node concept="p0ZFU" id="BpL3Oh1$SV" role="p0ZFf">
            <node concept="p0ZFY" id="BpL3Oh1$SY" role="p0ZFo">
              <ref role="peofi" node="BpL3Oh1$Sm" resolve="output" />
            </node>
            <node concept="pevns" id="BpL3Oh1$SZ" role="p0ZFr" />
          </node>
          <node concept="p0ZFR" id="BpL3Oh1$T0" role="p0ZFe">
            <node concept="p0ZFS" id="BpL3Oh1$T4" role="p0ZFm">
              <property role="p0ZFt" value="!=" />
              <node concept="p0ZFU" id="BpL3Oh1$T7" role="p0ZFs">
                <node concept="p0ZFY" id="BpL3Oh1$Ta" role="p0ZFo">
                  <ref role="peofi" node="BpL3Oh1$Sj" resolve="mask" />
                </node>
                <node concept="pevns" id="BpL3Oh1$Tb" role="p0ZFr" />
              </node>
              <node concept="p0ZFT" id="BpL3Oh1$Tc" role="p0ZFv">
                <property role="p0ZFy" value="0" />
              </node>
            </node>
            <node concept="p0ZFU" id="BpL3Oh1$Td" role="p0ZFh">
              <node concept="p0ZFY" id="BpL3Oh1$Tg" role="p0ZFo">
                <ref role="peofi" node="BpL3Oh1$Sg" resolve="pixels" />
              </node>
              <node concept="pevns" id="BpL3Oh1$Th" role="p0ZFr" />
            </node>
            <node concept="p0ZFT" id="BpL3Oh1$Ti" role="p0ZFg">
              <property role="p0ZFy" value="0" />
            </node>
          </node>
        </node>
      </node>
    </node>
  </node>
  <node concept="pevno" id="BpL3Oh1$Tj">
    <property role="TrG5h" value="cuda_masked_copy_launch" />
    <ref role="pevni" node="BpL3Oh1$S9" resolve="cuda_masked_copy" />
    <node concept="pevnr" id="BpL3Oh1$To" role="pevng">
      <property role="TrG5h" value="pixels" />
      <property role="pevnc" value="input" />
      <ref role="paaHl" node="BpL3Oh1$Tx" />
      <node concept="p1rTi" id="BpL3Oh1$Tq" role="pevnf" />
    </node>
    <node concept="pevnr" id="BpL3Oh1$Tr" role="pevng">
      <property role="TrG5h" value="mask" />
      <property role="pevnc" value="input" />
      <ref role="paaHl" node="BpL3Oh1$Tx" />
      <node concept="p1rTi" id="BpL3Oh1$Tt" role="pevnf" />
    </node>
    <node concept="pevnr" id="BpL3Oh1$Tu" role="pevng">
      <property role="TrG5h" value="output" />
      <property role="pevnc" value="output" />
      <ref role="paaHl" node="BpL3Oh1$Tx" />
      <node concept="p1rTc" id="BpL3Oh1$Tw" role="pevnf" />
    </node>
    <node concept="pevnr" id="BpL3Oh1$Tx" role="pevng">
      <property role="TrG5h" value="count" />
      <property role="pevnc" value="shape" />
      <node concept="p1rTf" id="BpL3Oh1$Tz" role="pevnf" />
    </node>
    <node concept="pevnq" id="BpL3Oh1$T$" role="pevnj">
      <property role="pevn9" value="64" />
      <property role="pevn8" value="true" />
      <ref role="pevna" node="BpL3Oh1$Sd" resolve="masked_copy" />
      <ref role="pevn5" node="BpL3Oh1$Tx" resolve="count" />
      <node concept="pevnl" id="BpL3Oh1$TA" role="pevnb">
        <ref role="pevn4" node="BpL3Oh1$Sg" resolve="pixels" />
        <ref role="pevn7" node="BpL3Oh1$To" resolve="pixels" />
      </node>
      <node concept="pevnl" id="BpL3Oh1$TB" role="pevnb">
        <ref role="pevn4" node="BpL3Oh1$Sj" resolve="mask" />
        <ref role="pevn7" node="BpL3Oh1$Tr" resolve="mask" />
      </node>
      <node concept="pevnl" id="BpL3Oh1$TC" role="pevnb">
        <ref role="pevn4" node="BpL3Oh1$Sm" resolve="output" />
        <ref role="pevn7" node="BpL3Oh1$Tu" resolve="output" />
      </node>
      <node concept="pevnl" id="BpL3Oh1$TD" role="pevnb">
        <ref role="pevn4" node="BpL3Oh1$Sp" resolve="count" />
        <ref role="pevn7" node="BpL3Oh1$Tx" resolve="count" />
      </node>
    </node>
  </node>
  <node concept="p0ZE3" id="BpL3Oh1_Be">
    <property role="TrG5h" value="cuda_cli_demo" />
    <node concept="p0ZE2" id="BpL3Oh1_Bi" role="p0ZFJ">
      <property role="TrG5h" value="masked_copy" />
      <property role="p0ZFD" value="true" />
      <node concept="p0ZFX" id="BpL3Oh1_Bl" role="p0ZFF">
        <property role="TrG5h" value="pixels" />
        <property role="p0ZFB" value="0" />
        <property role="p0ZFx" value="true" />
        <node concept="p1rTi" id="BpL3Oh1_Bn" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3Oh1_Bo" role="p0ZFF">
        <property role="TrG5h" value="mask" />
        <property role="p0ZFB" value="1" />
        <property role="p0ZFx" value="true" />
        <node concept="p1rTi" id="BpL3Oh1_Bq" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3Oh1_Br" role="p0ZFF">
        <property role="TrG5h" value="output" />
        <property role="p0ZFB" value="2" />
        <property role="p0ZFx" value="true" />
        <node concept="p1rTc" id="BpL3Oh1_Bt" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="BpL3Oh1_Bu" role="p0ZFF">
        <property role="TrG5h" value="count" />
        <property role="p0ZFB" value="3" />
        <property role="p0ZFx" value="true" />
        <node concept="p1rTf" id="BpL3Oh1_Bw" role="p1kZ1" />
      </node>
      <node concept="p1rTg" id="BpL3Oh1_Bx" role="p1kZ5" />
      <node concept="pevnv" id="BpL3Oh1_By" role="peofa" />
      <node concept="p0ZFQ" id="BpL3Oh1_B$" role="p0ZFE">
        <node concept="p0ZFM" id="BpL3Oh1_B_" role="p0ZFj">
          <node concept="p0ZFS" id="BpL3Oh1_BC" role="p0ZF8">
            <property role="p0ZFt" value="&gt;=" />
            <node concept="pevns" id="BpL3Oh1_BF" role="p0ZFs" />
            <node concept="p0ZFS" id="BpL3Oh1_BG" role="p0ZFv">
              <property role="p0ZFt" value="*" />
              <node concept="p0ZFS" id="BpL3Oh1_BJ" role="p0ZFs">
                <property role="p0ZFt" value="*" />
                <node concept="p0ZFP" id="BpL3Oh1_BM" role="p0ZFs">
                  <property role="p0ZFq" value="x" />
                  <node concept="p0ZFY" id="BpL3Oh1_BO" role="p0ZFl">
                    <ref role="peofi" node="BpL3Oh1_Bu" resolve="count" />
                  </node>
                </node>
                <node concept="p0ZFP" id="BpL3Oh1_BP" role="p0ZFv">
                  <property role="p0ZFq" value="y" />
                  <node concept="p0ZFY" id="BpL3Oh1_BR" role="p0ZFl">
                    <ref role="peofi" node="BpL3Oh1_Bu" resolve="count" />
                  </node>
                </node>
              </node>
              <node concept="p0ZFP" id="BpL3Oh1_BS" role="p0ZFv">
                <property role="p0ZFq" value="z" />
                <node concept="p0ZFY" id="BpL3Oh1_BU" role="p0ZFl">
                  <ref role="peofi" node="BpL3Oh1_Bu" resolve="count" />
                </node>
              </node>
            </node>
          </node>
          <node concept="p0ZFQ" id="BpL3Oh1_BV" role="p0ZFb">
            <node concept="p0ZFG" id="BpL3Oh1_BW" role="p0ZFj" />
          </node>
        </node>
        <node concept="p0ZFK" id="BpL3Oh1_BX" role="p0ZFj">
          <node concept="p0ZFU" id="BpL3Oh1_C0" role="p0ZFf">
            <node concept="p0ZFY" id="BpL3Oh1_C3" role="p0ZFo">
              <ref role="peofi" node="BpL3Oh1_Br" resolve="output" />
            </node>
            <node concept="pevns" id="BpL3Oh1_C4" role="p0ZFr" />
          </node>
          <node concept="p0ZFR" id="BpL3Oh1_C5" role="p0ZFe">
            <node concept="p0ZFS" id="BpL3Oh1_C9" role="p0ZFm">
              <property role="p0ZFt" value="!=" />
              <node concept="p0ZFU" id="BpL3Oh1_Cc" role="p0ZFs">
                <node concept="p0ZFY" id="BpL3Oh1_Cf" role="p0ZFo">
                  <ref role="peofi" node="BpL3Oh1_Bo" resolve="mask" />
                </node>
                <node concept="pevns" id="BpL3Oh1_Cg" role="p0ZFr" />
              </node>
              <node concept="p0ZFT" id="BpL3Oh1_Ch" role="p0ZFv">
                <property role="p0ZFy" value="0" />
              </node>
            </node>
            <node concept="p0ZFU" id="BpL3Oh1_Ci" role="p0ZFh">
              <node concept="p0ZFY" id="BpL3Oh1_Cl" role="p0ZFo">
                <ref role="peofi" node="BpL3Oh1_Bl" resolve="pixels" />
              </node>
              <node concept="pevns" id="BpL3Oh1_Cm" role="p0ZFr" />
            </node>
            <node concept="p0ZFT" id="BpL3Oh1_Cn" role="p0ZFg">
              <property role="p0ZFy" value="0" />
            </node>
          </node>
        </node>
      </node>
    </node>
  </node>
  <node concept="pevno" id="BpL3Oh1_Co">
    <property role="TrG5h" value="cuda_cli_demo_launch" />
    <ref role="pevni" node="BpL3Oh1_Be" resolve="cuda_cli_demo" />
    <node concept="pevnr" id="BpL3Oh1_Ct" role="pevng">
      <property role="TrG5h" value="pixels" />
      <property role="pevnc" value="input" />
      <ref role="paaHl" node="BpL3Oh1_CA" />
      <node concept="p1rTi" id="BpL3Oh1_Cv" role="pevnf" />
    </node>
    <node concept="pevnr" id="BpL3Oh1_Cw" role="pevng">
      <property role="TrG5h" value="mask" />
      <property role="pevnc" value="input" />
      <ref role="paaHl" node="BpL3Oh1_CA" />
      <node concept="p1rTi" id="BpL3Oh1_Cy" role="pevnf" />
    </node>
    <node concept="pevnr" id="BpL3Oh1_Cz" role="pevng">
      <property role="TrG5h" value="output" />
      <property role="pevnc" value="output" />
      <ref role="paaHl" node="BpL3Oh1_CA" />
      <node concept="p1rTc" id="BpL3Oh1_C_" role="pevnf" />
    </node>
    <node concept="pevnr" id="BpL3Oh1_CA" role="pevng">
      <property role="TrG5h" value="count" />
      <property role="pevnc" value="shape" />
      <node concept="p1rTf" id="BpL3Oh1_CC" role="pevnf" />
    </node>
    <node concept="pevnq" id="BpL3Oh1_CD" role="pevnj">
      <property role="pevn9" value="64" />
      <property role="pevn8" value="true" />
      <ref role="pevna" node="BpL3Oh1_Bi" resolve="masked_copy" />
      <ref role="pevn5" node="BpL3Oh1_CA" resolve="count" />
      <node concept="pevnl" id="BpL3Oh1_CF" role="pevnb">
        <ref role="pevn4" node="BpL3Oh1_Bl" resolve="pixels" />
        <ref role="pevn7" node="BpL3Oh1_Ct" resolve="pixels" />
      </node>
      <node concept="pevnl" id="BpL3Oh1_CG" role="pevnb">
        <ref role="pevn4" node="BpL3Oh1_Bo" resolve="mask" />
        <ref role="pevn7" node="BpL3Oh1_Cw" resolve="mask" />
      </node>
      <node concept="pevnl" id="BpL3Oh1_CH" role="pevnb">
        <ref role="pevn4" node="BpL3Oh1_Br" resolve="output" />
        <ref role="pevn7" node="BpL3Oh1_Cz" resolve="output" />
      </node>
      <node concept="pevnl" id="BpL3Oh1_CI" role="pevnb">
        <ref role="pevn4" node="BpL3Oh1_Bu" resolve="count" />
        <ref role="pevn7" node="BpL3Oh1_CA" resolve="count" />
      </node>
    </node>
  </node>
  <node concept="p0ZE3" id="5$pbNpTO3Pi">
    <property role="TrG5h" value="float_masked_scale" />
    <node concept="p0ZE2" id="5$pbNpTO3Pm" role="p0ZFJ">
      <property role="p0ZFD" value="true" />
      <property role="TrG5h" value="masked_copy" />
      <node concept="p0ZFX" id="5$pbNpTO3Pp" role="p0ZFF">
        <property role="p0ZFB" value="0" />
        <property role="p0ZFx" value="true" />
        <property role="TrG5h" value="pixels" />
        <node concept="wDpUf" id="5$pbNpTO3Pr" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="5$pbNpTO3Ps" role="p0ZFF">
        <property role="p0ZFB" value="1" />
        <property role="p0ZFx" value="true" />
        <property role="TrG5h" value="mask" />
        <node concept="p1rTi" id="5$pbNpTO3Pu" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="5$pbNpTO3Pv" role="p0ZFF">
        <property role="p0ZFB" value="2" />
        <property role="p0ZFx" value="true" />
        <property role="TrG5h" value="output" />
        <node concept="wDpUc" id="5$pbNpTO3Px" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="5$pbNpTO3Py" role="p0ZFF">
        <property role="p0ZFB" value="3" />
        <property role="p0ZFx" value="true" />
        <property role="TrG5h" value="count" />
        <node concept="p1rTf" id="5$pbNpTO3P$" role="p1kZ1" />
      </node>
      <node concept="p0ZFQ" id="5$pbNpTO3P_" role="p0ZFE">
        <node concept="p0ZFM" id="5$pbNpTO3PA" role="p0ZFj">
          <node concept="p0ZFS" id="5$pbNpTO3PD" role="p0ZF8">
            <property role="p0ZFt" value="&gt;=" />
            <node concept="pevns" id="5$pbNpTO3PG" role="p0ZFs" />
            <node concept="p0ZFS" id="5$pbNpTO3PH" role="p0ZFv">
              <property role="p0ZFt" value="*" />
              <node concept="p0ZFS" id="5$pbNpTO3PK" role="p0ZFs">
                <property role="p0ZFt" value="*" />
                <node concept="p0ZFP" id="5$pbNpTO3PN" role="p0ZFs">
                  <property role="p0ZFq" value="x" />
                  <node concept="p0ZFY" id="5$pbNpTO3PP" role="p0ZFl">
                    <ref role="peofi" node="5$pbNpTO3Py" />
                  </node>
                </node>
                <node concept="p0ZFP" id="5$pbNpTO3PQ" role="p0ZFv">
                  <property role="p0ZFq" value="y" />
                  <node concept="p0ZFY" id="5$pbNpTO3PS" role="p0ZFl">
                    <ref role="peofi" node="5$pbNpTO3Py" />
                  </node>
                </node>
              </node>
              <node concept="p0ZFP" id="5$pbNpTO3PT" role="p0ZFv">
                <property role="p0ZFq" value="z" />
                <node concept="p0ZFY" id="5$pbNpTO3PV" role="p0ZFl">
                  <ref role="peofi" node="5$pbNpTO3Py" />
                </node>
              </node>
            </node>
          </node>
          <node concept="p0ZFQ" id="5$pbNpTO3PW" role="p0ZFb">
            <node concept="p0ZFG" id="5$pbNpTO3PX" role="p0ZFj" />
          </node>
        </node>
        <node concept="p0ZFK" id="5$pbNpTO3PY" role="p0ZFj">
          <node concept="p0ZFU" id="5$pbNpTO3Q1" role="p0ZFf">
            <node concept="p0ZFY" id="5$pbNpTO3Q4" role="p0ZFo">
              <ref role="peofi" node="5$pbNpTO3Pv" />
            </node>
            <node concept="pevns" id="5$pbNpTO3Q5" role="p0ZFr" />
          </node>
          <node concept="p0ZFR" id="5$pbNpTO3Q6" role="p0ZFe">
            <node concept="p0ZFS" id="5$pbNpTO3Qa" role="p0ZFm">
              <property role="p0ZFt" value="!=" />
              <node concept="p0ZFU" id="5$pbNpTO3Qd" role="p0ZFs">
                <node concept="p0ZFY" id="5$pbNpTO3Qg" role="p0ZFo">
                  <ref role="peofi" node="5$pbNpTO3Ps" />
                </node>
                <node concept="pevns" id="5$pbNpTO3Qh" role="p0ZFr" />
              </node>
              <node concept="p0ZFT" id="5$pbNpTO3Qi" role="p0ZFv">
                <property role="p0ZFy" value="0" />
              </node>
            </node>
            <node concept="p0ZFS" id="5$pbNpTO3Qj" role="p0ZFh">
              <property role="p0ZFt" value="*" />
              <node concept="p0ZFU" id="5$pbNpTO3Qm" role="p0ZFs">
                <node concept="p0ZFY" id="5$pbNpTO3Qp" role="p0ZFo">
                  <ref role="peofi" node="5$pbNpTO3Pp" />
                </node>
                <node concept="pevns" id="5$pbNpTO3Qq" role="p0ZFr" />
              </node>
              <node concept="wDpUa" id="5$pbNpTO3Qr" role="p0ZFv">
                <node concept="wDpUd" id="5$pbNpTOAyu" role="wDpU8">
                  <property role="wDpUb" value="0.5" />
                </node>
              </node>
            </node>
            <node concept="wDpUd" id="5$pbNpTO3Qu" role="p0ZFg">
              <property role="wDpUb" value="0.0" />
            </node>
          </node>
        </node>
      </node>
      <node concept="p1rTg" id="5$pbNpTO3Qv" role="p1kZ5" />
      <node concept="pevnv" id="5$pbNpTO3Qw" role="peofa" />
    </node>
  </node>
  <node concept="pevno" id="5$pbNpTO3Qx">
    <property role="TrG5h" value="float_masked_scale_launch" />
    <ref role="pevni" node="5$pbNpTO3Pi" />
    <node concept="pevnr" id="5$pbNpTO3QA" role="pevng">
      <property role="pevnc" value="input" />
      <property role="TrG5h" value="pixels" />
      <ref role="paaHl" node="5$pbNpTO3QJ" />
      <node concept="wDpUf" id="5$pbNpTO3QC" role="pevnf" />
    </node>
    <node concept="pevnr" id="5$pbNpTO3QD" role="pevng">
      <property role="pevnc" value="input" />
      <property role="TrG5h" value="mask" />
      <ref role="paaHl" node="5$pbNpTO3QJ" />
      <node concept="p1rTi" id="5$pbNpTO3QF" role="pevnf" />
    </node>
    <node concept="pevnr" id="5$pbNpTO3QG" role="pevng">
      <property role="pevnc" value="output" />
      <property role="TrG5h" value="output" />
      <ref role="paaHl" node="5$pbNpTO3QJ" />
      <node concept="wDpUc" id="5$pbNpTO3QI" role="pevnf" />
    </node>
    <node concept="pevnr" id="5$pbNpTO3QJ" role="pevng">
      <property role="pevnc" value="shape" />
      <property role="TrG5h" value="count" />
      <node concept="p1rTf" id="5$pbNpTO3QL" role="pevnf" />
    </node>
    <node concept="pevnq" id="5$pbNpTO3QM" role="pevnj">
      <property role="pevn9" value="64" />
      <property role="pevn8" value="true" />
      <ref role="pevna" node="5$pbNpTO3Pm" />
      <ref role="pevn5" node="5$pbNpTO3QJ" />
      <node concept="pevnl" id="5$pbNpTO3QO" role="pevnb">
        <ref role="pevn4" node="5$pbNpTO3Pp" />
        <ref role="pevn7" node="5$pbNpTO3QA" />
      </node>
      <node concept="pevnl" id="5$pbNpTO3QP" role="pevnb">
        <ref role="pevn4" node="5$pbNpTO3Ps" />
        <ref role="pevn7" node="5$pbNpTO3QD" />
      </node>
      <node concept="pevnl" id="5$pbNpTO3QQ" role="pevnb">
        <ref role="pevn4" node="5$pbNpTO3Pv" />
        <ref role="pevn7" node="5$pbNpTO3QG" />
      </node>
      <node concept="pevnl" id="5$pbNpTO3QR" role="pevnb">
        <ref role="pevn4" node="5$pbNpTO3Py" />
        <ref role="pevn7" node="5$pbNpTO3QJ" />
      </node>
    </node>
  </node>
  <node concept="p0ZE3" id="5$pbNpTQS7l">
    <property role="TrG5h" value="float_fma" />
    <node concept="p0ZE2" id="5$pbNpTQS7p" role="p0ZFJ">
      <property role="p0ZFD" value="true" />
      <property role="TrG5h" value="masked_copy" />
      <node concept="p0ZFX" id="5$pbNpTQS7s" role="p0ZFF">
        <property role="p0ZFB" value="0" />
        <property role="p0ZFx" value="true" />
        <property role="TrG5h" value="pixels" />
        <node concept="wDpUf" id="5$pbNpTQS7u" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="5$pbNpTQS7v" role="p0ZFF">
        <property role="p0ZFB" value="1" />
        <property role="p0ZFx" value="true" />
        <property role="TrG5h" value="mask" />
        <node concept="p1rTi" id="5$pbNpTQS7x" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="5$pbNpTQS7y" role="p0ZFF">
        <property role="p0ZFB" value="2" />
        <property role="p0ZFx" value="true" />
        <property role="TrG5h" value="output" />
        <node concept="wDpUc" id="5$pbNpTQS7$" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="5$pbNpTQS7_" role="p0ZFF">
        <property role="p0ZFB" value="3" />
        <property role="p0ZFx" value="true" />
        <property role="TrG5h" value="count" />
        <node concept="p1rTf" id="5$pbNpTQS7B" role="p1kZ1" />
      </node>
      <node concept="p0ZFQ" id="5$pbNpTQS7C" role="p0ZFE">
        <node concept="p0ZFM" id="5$pbNpTQS7D" role="p0ZFj">
          <node concept="p0ZFS" id="5$pbNpTQS7G" role="p0ZF8">
            <property role="p0ZFt" value="&gt;=" />
            <node concept="pevns" id="5$pbNpTQS7J" role="p0ZFs" />
            <node concept="p0ZFS" id="5$pbNpTQS7K" role="p0ZFv">
              <property role="p0ZFt" value="*" />
              <node concept="p0ZFS" id="5$pbNpTQS7N" role="p0ZFs">
                <property role="p0ZFt" value="*" />
                <node concept="p0ZFP" id="5$pbNpTQS7Q" role="p0ZFs">
                  <property role="p0ZFq" value="x" />
                  <node concept="p0ZFY" id="5$pbNpTQS7S" role="p0ZFl">
                    <ref role="peofi" node="5$pbNpTQS7_" />
                  </node>
                </node>
                <node concept="p0ZFP" id="5$pbNpTQS7T" role="p0ZFv">
                  <property role="p0ZFq" value="y" />
                  <node concept="p0ZFY" id="5$pbNpTQS7V" role="p0ZFl">
                    <ref role="peofi" node="5$pbNpTQS7_" />
                  </node>
                </node>
              </node>
              <node concept="p0ZFP" id="5$pbNpTQS7W" role="p0ZFv">
                <property role="p0ZFq" value="z" />
                <node concept="p0ZFY" id="5$pbNpTQS7Y" role="p0ZFl">
                  <ref role="peofi" node="5$pbNpTQS7_" />
                </node>
              </node>
            </node>
          </node>
          <node concept="p0ZFQ" id="5$pbNpTQS7Z" role="p0ZFb">
            <node concept="p0ZFG" id="5$pbNpTQS80" role="p0ZFj" />
          </node>
        </node>
        <node concept="p0ZFK" id="5$pbNpTQS81" role="p0ZFj">
          <node concept="p0ZFU" id="5$pbNpTQS84" role="p0ZFf">
            <node concept="p0ZFY" id="5$pbNpTQS87" role="p0ZFo">
              <ref role="peofi" node="5$pbNpTQS7y" />
            </node>
            <node concept="pevns" id="5$pbNpTQS88" role="p0ZFr" />
          </node>
          <node concept="p0ZFR" id="5$pbNpTQS89" role="p0ZFe">
            <node concept="p0ZFS" id="5$pbNpTQS8d" role="p0ZFm">
              <property role="p0ZFt" value="!=" />
              <node concept="p0ZFU" id="5$pbNpTQS8g" role="p0ZFs">
                <node concept="p0ZFY" id="5$pbNpTQS8j" role="p0ZFo">
                  <ref role="peofi" node="5$pbNpTQS7v" />
                </node>
                <node concept="pevns" id="5$pbNpTQS8k" role="p0ZFr" />
              </node>
              <node concept="p0ZFT" id="5$pbNpTQS8l" role="p0ZFv">
                <property role="p0ZFy" value="0" />
              </node>
            </node>
            <node concept="wNq4O" id="5$pbNpTQS8m" role="p0ZFh">
              <node concept="p0ZFU" id="5$pbNpTQS8q" role="wNq4P">
                <node concept="p0ZFY" id="5$pbNpTQS8t" role="p0ZFo">
                  <ref role="peofi" node="5$pbNpTQS7s" />
                </node>
                <node concept="pevns" id="5$pbNpTQS8u" role="p0ZFr" />
              </node>
              <node concept="wDpUd" id="5$pbNpTRqW_" role="wNq4M">
                <property role="wDpUb" value="0.99999988079071044921875" />
              </node>
              <node concept="p0ZFV" id="5$pbNpTQS8w" role="wNq4N">
                <property role="p0ZFu" value="-" />
                <node concept="wDpUd" id="5$pbNpTQS8y" role="p0ZFp">
                  <property role="wDpUb" value="1.0" />
                </node>
              </node>
            </node>
            <node concept="wDpUd" id="5$pbNpTQS8z" role="p0ZFg">
              <property role="wDpUb" value="0.0" />
            </node>
          </node>
        </node>
      </node>
      <node concept="p1rTg" id="5$pbNpTQS8$" role="p1kZ5" />
      <node concept="pevnv" id="5$pbNpTQS8_" role="peofa" />
    </node>
  </node>
  <node concept="pevno" id="5$pbNpTQS8A">
    <property role="TrG5h" value="float_fma_launch" />
    <ref role="pevni" node="5$pbNpTQS7l" />
    <node concept="pevnr" id="5$pbNpTQS8F" role="pevng">
      <property role="pevnc" value="input" />
      <property role="TrG5h" value="pixels" />
      <ref role="paaHl" node="5$pbNpTQS8O" />
      <node concept="wDpUf" id="5$pbNpTQS8H" role="pevnf" />
    </node>
    <node concept="pevnr" id="5$pbNpTQS8I" role="pevng">
      <property role="pevnc" value="input" />
      <property role="TrG5h" value="mask" />
      <ref role="paaHl" node="5$pbNpTQS8O" />
      <node concept="p1rTi" id="5$pbNpTQS8K" role="pevnf" />
    </node>
    <node concept="pevnr" id="5$pbNpTQS8L" role="pevng">
      <property role="pevnc" value="output" />
      <property role="TrG5h" value="output" />
      <ref role="paaHl" node="5$pbNpTQS8O" />
      <node concept="wDpUc" id="5$pbNpTQS8N" role="pevnf" />
    </node>
    <node concept="pevnr" id="5$pbNpTQS8O" role="pevng">
      <property role="pevnc" value="shape" />
      <property role="TrG5h" value="count" />
      <node concept="p1rTf" id="5$pbNpTQS8Q" role="pevnf" />
    </node>
    <node concept="pevnq" id="5$pbNpTQS8R" role="pevnj">
      <property role="pevn9" value="64" />
      <property role="pevn8" value="true" />
      <ref role="pevna" node="5$pbNpTQS7p" />
      <ref role="pevn5" node="5$pbNpTQS8O" />
      <node concept="pevnl" id="5$pbNpTQS8T" role="pevnb">
        <ref role="pevn4" node="5$pbNpTQS7s" />
        <ref role="pevn7" node="5$pbNpTQS8F" />
      </node>
      <node concept="pevnl" id="5$pbNpTQS8U" role="pevnb">
        <ref role="pevn4" node="5$pbNpTQS7v" />
        <ref role="pevn7" node="5$pbNpTQS8I" />
      </node>
      <node concept="pevnl" id="5$pbNpTQS8V" role="pevnb">
        <ref role="pevn4" node="5$pbNpTQS7y" />
        <ref role="pevn7" node="5$pbNpTQS8L" />
      </node>
      <node concept="pevnl" id="5$pbNpTQS8W" role="pevnb">
        <ref role="pevn4" node="5$pbNpTQS7_" />
        <ref role="pevn7" node="5$pbNpTQS8O" />
      </node>
    </node>
  </node>
  <node concept="p0ZE3" id="5$pbNpTW05d">
    <property role="TrG5h" value="half_mixed_scale" />
    <node concept="p0ZE2" id="5$pbNpTW05h" role="p0ZFJ">
      <property role="p0ZFD" value="true" />
      <property role="TrG5h" value="masked_copy" />
      <node concept="p0ZFX" id="5$pbNpTW05k" role="p0ZFF">
        <property role="p0ZFB" value="0" />
        <property role="p0ZFx" value="true" />
        <property role="TrG5h" value="pixels" />
        <node concept="wLJn7" id="5$pbNpTW05m" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="5$pbNpTW05n" role="p0ZFF">
        <property role="p0ZFB" value="1" />
        <property role="p0ZFx" value="true" />
        <property role="TrG5h" value="mask" />
        <node concept="p1rTi" id="5$pbNpTW05p" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="5$pbNpTW05q" role="p0ZFF">
        <property role="p0ZFB" value="2" />
        <property role="p0ZFx" value="true" />
        <property role="TrG5h" value="output" />
        <node concept="wLJn4" id="5$pbNpTW05s" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="5$pbNpTW05t" role="p0ZFF">
        <property role="p0ZFB" value="3" />
        <property role="p0ZFx" value="true" />
        <property role="TrG5h" value="count" />
        <node concept="p1rTf" id="5$pbNpTW05v" role="p1kZ1" />
      </node>
      <node concept="p0ZFQ" id="5$pbNpTW05w" role="p0ZFE">
        <node concept="p0ZFM" id="5$pbNpTW05x" role="p0ZFj">
          <node concept="p0ZFS" id="5$pbNpTW05$" role="p0ZF8">
            <property role="p0ZFt" value="&gt;=" />
            <node concept="pevns" id="5$pbNpTW05B" role="p0ZFs" />
            <node concept="p0ZFS" id="5$pbNpTW05C" role="p0ZFv">
              <property role="p0ZFt" value="*" />
              <node concept="p0ZFS" id="5$pbNpTW05F" role="p0ZFs">
                <property role="p0ZFt" value="*" />
                <node concept="p0ZFP" id="5$pbNpTW05I" role="p0ZFs">
                  <property role="p0ZFq" value="x" />
                  <node concept="p0ZFY" id="5$pbNpTW05K" role="p0ZFl">
                    <ref role="peofi" node="5$pbNpTW05t" />
                  </node>
                </node>
                <node concept="p0ZFP" id="5$pbNpTW05L" role="p0ZFv">
                  <property role="p0ZFq" value="y" />
                  <node concept="p0ZFY" id="5$pbNpTW05N" role="p0ZFl">
                    <ref role="peofi" node="5$pbNpTW05t" />
                  </node>
                </node>
              </node>
              <node concept="p0ZFP" id="5$pbNpTW05O" role="p0ZFv">
                <property role="p0ZFq" value="z" />
                <node concept="p0ZFY" id="5$pbNpTW05Q" role="p0ZFl">
                  <ref role="peofi" node="5$pbNpTW05t" />
                </node>
              </node>
            </node>
          </node>
          <node concept="p0ZFQ" id="5$pbNpTW05R" role="p0ZFb">
            <node concept="p0ZFG" id="5$pbNpTW05S" role="p0ZFj" />
          </node>
        </node>
        <node concept="p0ZFK" id="5$pbNpTW05T" role="p0ZFj">
          <node concept="p0ZFU" id="5$pbNpTW05W" role="p0ZFf">
            <node concept="p0ZFY" id="5$pbNpTW05Z" role="p0ZFo">
              <ref role="peofi" node="5$pbNpTW05q" />
            </node>
            <node concept="pevns" id="5$pbNpTW060" role="p0ZFr" />
          </node>
          <node concept="wLJn5" id="5$pbNpTW061" role="p0ZFe">
            <node concept="p0ZFR" id="5$pbNpTW063" role="wLJn2">
              <node concept="p0ZFS" id="5$pbNpTW067" role="p0ZFm">
                <property role="p0ZFt" value="!=" />
                <node concept="p0ZFU" id="5$pbNpTW06a" role="p0ZFs">
                  <node concept="p0ZFY" id="5$pbNpTW06d" role="p0ZFo">
                    <ref role="peofi" node="5$pbNpTW05n" />
                  </node>
                  <node concept="pevns" id="5$pbNpTW06e" role="p0ZFr" />
                </node>
                <node concept="p0ZFT" id="5$pbNpTW06f" role="p0ZFv">
                  <property role="p0ZFy" value="0" />
                </node>
              </node>
              <node concept="wNq4O" id="5$pbNpTW06g" role="p0ZFh">
                <node concept="wDpUa" id="5$pbNpTW06k" role="wNq4P">
                  <node concept="p0ZFU" id="5$pbNpTW06m" role="wDpU8">
                    <node concept="p0ZFY" id="5$pbNpTW06p" role="p0ZFo">
                      <ref role="peofi" node="5$pbNpTW05k" />
                    </node>
                    <node concept="pevns" id="5$pbNpTW06q" role="p0ZFr" />
                  </node>
                </node>
                <node concept="wDpUd" id="5$pbNpTW06r" role="wNq4M">
                  <property role="wDpUb" value="1.0" />
                </node>
                <node concept="wDpUd" id="5$pbNpTWzi6" role="wNq4N">
                  <property role="wDpUb" value="0.00048828125" />
                </node>
              </node>
              <node concept="wDpUd" id="5$pbNpTW06t" role="p0ZFg">
                <property role="wDpUb" value="0.0" />
              </node>
            </node>
          </node>
        </node>
      </node>
      <node concept="p1rTg" id="5$pbNpTW06u" role="p1kZ5" />
      <node concept="pevnv" id="5$pbNpTW06v" role="peofa" />
    </node>
  </node>
  <node concept="pevno" id="5$pbNpTW06w">
    <property role="TrG5h" value="half_mixed_scale_launch" />
    <ref role="pevni" node="5$pbNpTW05d" />
    <node concept="pevnr" id="5$pbNpTW06_" role="pevng">
      <property role="pevnc" value="input" />
      <property role="TrG5h" value="pixels" />
      <ref role="paaHl" node="5$pbNpTW06I" />
      <node concept="wLJn7" id="5$pbNpTW06B" role="pevnf" />
    </node>
    <node concept="pevnr" id="5$pbNpTW06C" role="pevng">
      <property role="pevnc" value="input" />
      <property role="TrG5h" value="mask" />
      <ref role="paaHl" node="5$pbNpTW06I" />
      <node concept="p1rTi" id="5$pbNpTW06E" role="pevnf" />
    </node>
    <node concept="pevnr" id="5$pbNpTW06F" role="pevng">
      <property role="pevnc" value="output" />
      <property role="TrG5h" value="output" />
      <ref role="paaHl" node="5$pbNpTW06I" />
      <node concept="wLJn4" id="5$pbNpTW06H" role="pevnf" />
    </node>
    <node concept="pevnr" id="5$pbNpTW06I" role="pevng">
      <property role="pevnc" value="shape" />
      <property role="TrG5h" value="count" />
      <node concept="p1rTf" id="5$pbNpTW06K" role="pevnf" />
    </node>
    <node concept="pevnq" id="5$pbNpTW06L" role="pevnj">
      <property role="pevn9" value="64" />
      <property role="pevn8" value="true" />
      <ref role="pevna" node="5$pbNpTW05h" />
      <ref role="pevn5" node="5$pbNpTW06I" />
      <node concept="pevnl" id="5$pbNpTW06N" role="pevnb">
        <ref role="pevn4" node="5$pbNpTW05k" />
        <ref role="pevn7" node="5$pbNpTW06_" />
      </node>
      <node concept="pevnl" id="5$pbNpTW06O" role="pevnb">
        <ref role="pevn4" node="5$pbNpTW05n" />
        <ref role="pevn7" node="5$pbNpTW06C" />
      </node>
      <node concept="pevnl" id="5$pbNpTW06P" role="pevnb">
        <ref role="pevn4" node="5$pbNpTW05q" />
        <ref role="pevn7" node="5$pbNpTW06F" />
      </node>
      <node concept="pevnl" id="5$pbNpTW06Q" role="pevnb">
        <ref role="pevn4" node="5$pbNpTW05t" />
        <ref role="pevn7" node="5$pbNpTW06I" />
      </node>
    </node>
  </node>
  <node concept="p0ZE3" id="5$pbNpTZuJ$">
    <property role="TrG5h" value="float_window_sum" />
    <node concept="p0ZE2" id="5$pbNpTZuJC" role="p0ZFJ">
      <property role="p0ZFD" value="true" />
      <property role="TrG5h" value="masked_copy" />
      <node concept="p0ZFX" id="5$pbNpTZuJF" role="p0ZFF">
        <property role="p0ZFB" value="0" />
        <property role="p0ZFx" value="true" />
        <property role="TrG5h" value="pixels" />
        <node concept="wDpUf" id="5$pbNpTZuJH" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="5$pbNpTZuJI" role="p0ZFF">
        <property role="p0ZFB" value="1" />
        <property role="p0ZFx" value="true" />
        <property role="TrG5h" value="mask" />
        <node concept="p1rTi" id="5$pbNpTZuJK" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="5$pbNpTZuJL" role="p0ZFF">
        <property role="p0ZFB" value="2" />
        <property role="p0ZFx" value="true" />
        <property role="TrG5h" value="output" />
        <node concept="wDpUc" id="5$pbNpTZuJN" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="5$pbNpTZuJO" role="p0ZFF">
        <property role="p0ZFB" value="3" />
        <property role="p0ZFx" value="true" />
        <property role="TrG5h" value="count" />
        <node concept="p1rTf" id="5$pbNpTZuJQ" role="p1kZ1" />
      </node>
      <node concept="p0ZFQ" id="5$pbNpTZuJR" role="p0ZFE">
        <node concept="p0ZFM" id="5$pbNpTZuJS" role="p0ZFj">
          <node concept="p0ZFS" id="5$pbNpTZuJV" role="p0ZF8">
            <property role="p0ZFt" value="&gt;=" />
            <node concept="pevns" id="5$pbNpTZuJY" role="p0ZFs" />
            <node concept="p0ZFS" id="5$pbNpTZuJZ" role="p0ZFv">
              <property role="p0ZFt" value="*" />
              <node concept="p0ZFS" id="5$pbNpTZuK2" role="p0ZFs">
                <property role="p0ZFt" value="*" />
                <node concept="p0ZFP" id="5$pbNpTZuK5" role="p0ZFs">
                  <property role="p0ZFq" value="x" />
                  <node concept="p0ZFY" id="5$pbNpTZuK7" role="p0ZFl">
                    <ref role="peofi" node="5$pbNpTZuJO" />
                  </node>
                </node>
                <node concept="p0ZFP" id="5$pbNpTZuK8" role="p0ZFv">
                  <property role="p0ZFq" value="y" />
                  <node concept="p0ZFY" id="5$pbNpTZuKa" role="p0ZFl">
                    <ref role="peofi" node="5$pbNpTZuJO" />
                  </node>
                </node>
              </node>
              <node concept="p0ZFP" id="5$pbNpTZuKb" role="p0ZFv">
                <property role="p0ZFq" value="z" />
                <node concept="p0ZFY" id="5$pbNpTZuKd" role="p0ZFl">
                  <ref role="peofi" node="5$pbNpTZuJO" />
                </node>
              </node>
            </node>
          </node>
          <node concept="p0ZFQ" id="5$pbNpTZuKe" role="p0ZFb">
            <node concept="p0ZFG" id="5$pbNpTZuKf" role="p0ZFj" />
          </node>
        </node>
        <node concept="p0ZFK" id="5$pbNpTZuKg" role="p0ZFj">
          <node concept="p0ZFU" id="5$pbNpTZuKj" role="p0ZFf">
            <node concept="p0ZFY" id="5$pbNpTZuKm" role="p0ZFo">
              <ref role="peofi" node="5$pbNpTZuJL" />
            </node>
            <node concept="pevns" id="5$pbNpTZuKn" role="p0ZFr" />
          </node>
          <node concept="p0ZFR" id="5$pbNpTZuKo" role="p0ZFe">
            <node concept="p0ZFS" id="5$pbNpTZuKs" role="p0ZFm">
              <property role="p0ZFt" value="!=" />
              <node concept="p0ZFU" id="5$pbNpTZuKv" role="p0ZFs">
                <node concept="p0ZFY" id="5$pbNpTZuKy" role="p0ZFo">
                  <ref role="peofi" node="5$pbNpTZuJI" />
                </node>
                <node concept="pevns" id="5$pbNpTZuKz" role="p0ZFr" />
              </node>
              <node concept="p0ZFT" id="5$pbNpTZuK$" role="p0ZFv">
                <property role="p0ZFy" value="0" />
              </node>
            </node>
            <node concept="wVkuE" id="5$pbNpTZuK_" role="p0ZFh">
              <node concept="p0ZFY" id="5$pbNpTZuKD" role="wVkuF">
                <ref role="peofi" node="5$pbNpTZuJF" />
              </node>
              <node concept="pevns" id="5$pbNpTZuKE" role="wVkuC" />
              <node concept="pevnu" id="5$pbNpTZuKF" role="wVkuD">
                <node concept="p0ZFT" id="5$pbNpTZuKI" role="pevnk">
                  <property role="p0ZFy" value="4" />
                </node>
                <node concept="p0ZFS" id="5$pbNpTZuKJ" role="pevnn">
                  <property role="p0ZFt" value="-" />
                  <node concept="p0ZFS" id="5$pbNpTZuKM" role="p0ZFs">
                    <property role="p0ZFt" value="*" />
                    <node concept="p0ZFS" id="5$pbNpTZuKP" role="p0ZFs">
                      <property role="p0ZFt" value="*" />
                      <node concept="p0ZFP" id="5$pbNpTZuKS" role="p0ZFs">
                        <property role="p0ZFq" value="x" />
                        <node concept="p0ZFY" id="5$pbNpTZuKU" role="p0ZFl">
                          <ref role="peofi" node="5$pbNpTZuJO" />
                        </node>
                      </node>
                      <node concept="p0ZFP" id="5$pbNpTZuKV" role="p0ZFv">
                        <property role="p0ZFq" value="y" />
                        <node concept="p0ZFY" id="5$pbNpTZuKX" role="p0ZFl">
                          <ref role="peofi" node="5$pbNpTZuJO" />
                        </node>
                      </node>
                    </node>
                    <node concept="p0ZFP" id="5$pbNpTZuKY" role="p0ZFv">
                      <property role="p0ZFq" value="z" />
                      <node concept="p0ZFY" id="5$pbNpTZuL0" role="p0ZFl">
                        <ref role="peofi" node="5$pbNpTZuJO" />
                      </node>
                    </node>
                  </node>
                  <node concept="pevns" id="5$pbNpTZuL1" role="p0ZFv" />
                </node>
              </node>
            </node>
            <node concept="wDpUd" id="5$pbNpTZuL2" role="p0ZFg">
              <property role="wDpUb" value="0.0" />
            </node>
          </node>
        </node>
      </node>
      <node concept="p1rTg" id="5$pbNpTZuL3" role="p1kZ5" />
      <node concept="pevnv" id="5$pbNpTZuL4" role="peofa" />
    </node>
  </node>
  <node concept="pevno" id="5$pbNpTZuL5">
    <property role="TrG5h" value="float_window_sum_launch" />
    <ref role="pevni" node="5$pbNpTZuJ$" />
    <node concept="pevnr" id="5$pbNpTZuLa" role="pevng">
      <property role="pevnc" value="input" />
      <property role="TrG5h" value="pixels" />
      <ref role="paaHl" node="5$pbNpTZuLj" />
      <node concept="wDpUf" id="5$pbNpTZuLc" role="pevnf" />
    </node>
    <node concept="pevnr" id="5$pbNpTZuLd" role="pevng">
      <property role="pevnc" value="input" />
      <property role="TrG5h" value="mask" />
      <ref role="paaHl" node="5$pbNpTZuLj" />
      <node concept="p1rTi" id="5$pbNpTZuLf" role="pevnf" />
    </node>
    <node concept="pevnr" id="5$pbNpTZuLg" role="pevng">
      <property role="pevnc" value="output" />
      <property role="TrG5h" value="output" />
      <ref role="paaHl" node="5$pbNpTZuLj" />
      <node concept="wDpUc" id="5$pbNpTZuLi" role="pevnf" />
    </node>
    <node concept="pevnr" id="5$pbNpTZuLj" role="pevng">
      <property role="pevnc" value="shape" />
      <property role="TrG5h" value="count" />
      <node concept="p1rTf" id="5$pbNpTZuLl" role="pevnf" />
    </node>
    <node concept="pevnq" id="5$pbNpTZuLm" role="pevnj">
      <property role="pevn9" value="64" />
      <property role="pevn8" value="true" />
      <ref role="pevna" node="5$pbNpTZuJC" />
      <ref role="pevn5" node="5$pbNpTZuLj" />
      <node concept="pevnl" id="5$pbNpTZuLo" role="pevnb">
        <ref role="pevn4" node="5$pbNpTZuJF" />
        <ref role="pevn7" node="5$pbNpTZuLa" />
      </node>
      <node concept="pevnl" id="5$pbNpTZuLp" role="pevnb">
        <ref role="pevn4" node="5$pbNpTZuJI" />
        <ref role="pevn7" node="5$pbNpTZuLd" />
      </node>
      <node concept="pevnl" id="5$pbNpTZuLq" role="pevnb">
        <ref role="pevn4" node="5$pbNpTZuJL" />
        <ref role="pevn7" node="5$pbNpTZuLg" />
      </node>
      <node concept="pevnl" id="5$pbNpTZuLr" role="pevnb">
        <ref role="pevn4" node="5$pbNpTZuJO" />
        <ref role="pevn7" node="5$pbNpTZuLj" />
      </node>
    </node>
  </node>
  <node concept="p0ZE3" id="5$pbNpU3xZK">
    <property role="TrG5h" value="float_group_sum" />
    <node concept="p0ZE2" id="5$pbNpU3xZO" role="p0ZFJ">
      <property role="p0ZFD" value="true" />
      <property role="TrG5h" value="masked_copy" />
      <node concept="p0ZFX" id="5$pbNpU3xZR" role="p0ZFF">
        <property role="p0ZFB" value="0" />
        <property role="p0ZFx" value="true" />
        <property role="TrG5h" value="pixels" />
        <node concept="wDpUf" id="5$pbNpU3xZT" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="5$pbNpU3xZU" role="p0ZFF">
        <property role="p0ZFB" value="1" />
        <property role="p0ZFx" value="true" />
        <property role="TrG5h" value="mask" />
        <node concept="p1rTi" id="5$pbNpU3xZW" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="5$pbNpU3xZX" role="p0ZFF">
        <property role="p0ZFB" value="2" />
        <property role="p0ZFx" value="true" />
        <property role="TrG5h" value="output" />
        <node concept="wDpUc" id="5$pbNpU3xZZ" role="p1kZ1" />
      </node>
      <node concept="p0ZFX" id="5$pbNpU3y00" role="p0ZFF">
        <property role="p0ZFB" value="3" />
        <property role="p0ZFx" value="true" />
        <property role="TrG5h" value="count" />
        <node concept="p1rTf" id="5$pbNpU3y02" role="p1kZ1" />
      </node>
      <node concept="p0ZFQ" id="5$pbNpU3y03" role="p0ZFE">
        <node concept="p0ZFL" id="5$pbNpU3y04" role="p0ZFj">
          <property role="TrG5h" value="group_sum" />
          <node concept="wDpUe" id="5$pbNpU3y07" role="p1kwX" />
          <node concept="z6OWV" id="5$pbNpU3y08" role="p0ZFd">
            <node concept="p0ZFR" id="5$pbNpU3y0a" role="z6OWS">
              <node concept="p0ZFS" id="5$pbNpU3y0e" role="p0ZFm">
                <property role="p0ZFt" value="&lt;" />
                <node concept="pevns" id="5$pbNpU3y0h" role="p0ZFs" />
                <node concept="p0ZFS" id="5$pbNpU3y0i" role="p0ZFv">
                  <property role="p0ZFt" value="*" />
                  <node concept="p0ZFS" id="5$pbNpU3y0l" role="p0ZFs">
                    <property role="p0ZFt" value="*" />
                    <node concept="p0ZFP" id="5$pbNpU3y0o" role="p0ZFs">
                      <property role="p0ZFq" value="x" />
                      <node concept="p0ZFY" id="5$pbNpU3y0q" role="p0ZFl">
                        <ref role="peofi" node="5$pbNpU3y00" />
                      </node>
                    </node>
                    <node concept="p0ZFP" id="5$pbNpU3y0r" role="p0ZFv">
                      <property role="p0ZFq" value="y" />
                      <node concept="p0ZFY" id="5$pbNpU3y0t" role="p0ZFl">
                        <ref role="peofi" node="5$pbNpU3y00" />
                      </node>
                    </node>
                  </node>
                  <node concept="p0ZFP" id="5$pbNpU3y0u" role="p0ZFv">
                    <property role="p0ZFq" value="z" />
                    <node concept="p0ZFY" id="5$pbNpU3y0w" role="p0ZFl">
                      <ref role="peofi" node="5$pbNpU3y00" />
                    </node>
                  </node>
                </node>
              </node>
              <node concept="p0ZFR" id="5$pbNpU3y0x" role="p0ZFh">
                <node concept="p0ZFS" id="5$pbNpU3y0_" role="p0ZFm">
                  <property role="p0ZFt" value="!=" />
                  <node concept="p0ZFU" id="5$pbNpU3y0C" role="p0ZFs">
                    <node concept="p0ZFY" id="5$pbNpU3y0F" role="p0ZFo">
                      <ref role="peofi" node="5$pbNpU3xZU" />
                    </node>
                    <node concept="pevns" id="5$pbNpU3y0G" role="p0ZFr" />
                  </node>
                  <node concept="p0ZFT" id="5$pbNpU3y0H" role="p0ZFv">
                    <property role="p0ZFy" value="0" />
                  </node>
                </node>
                <node concept="p0ZFS" id="5$pbNpU3y0I" role="p0ZFh">
                  <property role="p0ZFt" value="*" />
                  <node concept="p0ZFU" id="5$pbNpU3y0L" role="p0ZFs">
                    <node concept="p0ZFY" id="5$pbNpU3y0O" role="p0ZFo">
                      <ref role="peofi" node="5$pbNpU3xZR" />
                    </node>
                    <node concept="pevns" id="5$pbNpU3y0P" role="p0ZFr" />
                  </node>
                  <node concept="wDpUd" id="5$pbNpU3y0Q" role="p0ZFv">
                    <property role="wDpUb" value="0.5" />
                  </node>
                </node>
                <node concept="wDpUd" id="5$pbNpU3y0R" role="p0ZFg">
                  <property role="wDpUb" value="0.0" />
                </node>
              </node>
              <node concept="wDpUd" id="5$pbNpU3y0S" role="p0ZFg">
                <property role="wDpUb" value="0.0" />
              </node>
            </node>
          </node>
        </node>
        <node concept="p0ZFM" id="5$pbNpU3y0T" role="p0ZFj">
          <node concept="p0ZFS" id="5$pbNpU3y0W" role="p0ZF8">
            <property role="p0ZFt" value="&lt;" />
            <node concept="pevns" id="5$pbNpU3y0Z" role="p0ZFs" />
            <node concept="p0ZFS" id="5$pbNpU3y10" role="p0ZFv">
              <property role="p0ZFt" value="*" />
              <node concept="p0ZFS" id="5$pbNpU3y13" role="p0ZFs">
                <property role="p0ZFt" value="*" />
                <node concept="p0ZFP" id="5$pbNpU3y16" role="p0ZFs">
                  <property role="p0ZFq" value="x" />
                  <node concept="p0ZFY" id="5$pbNpU3y18" role="p0ZFl">
                    <ref role="peofi" node="5$pbNpU3y00" />
                  </node>
                </node>
                <node concept="p0ZFP" id="5$pbNpU3y19" role="p0ZFv">
                  <property role="p0ZFq" value="y" />
                  <node concept="p0ZFY" id="5$pbNpU3y1b" role="p0ZFl">
                    <ref role="peofi" node="5$pbNpU3y00" />
                  </node>
                </node>
              </node>
              <node concept="p0ZFP" id="5$pbNpU3y1c" role="p0ZFv">
                <property role="p0ZFq" value="z" />
                <node concept="p0ZFY" id="5$pbNpU3y1e" role="p0ZFl">
                  <ref role="peofi" node="5$pbNpU3y00" />
                </node>
              </node>
            </node>
          </node>
          <node concept="p0ZFQ" id="5$pbNpU3y1f" role="p0ZFb">
            <node concept="p0ZFK" id="5$pbNpU3y1g" role="p0ZFj">
              <node concept="p0ZFU" id="5$pbNpU3y1j" role="p0ZFf">
                <node concept="p0ZFY" id="5$pbNpU3y1m" role="p0ZFo">
                  <ref role="peofi" node="5$pbNpU3xZX" />
                </node>
                <node concept="pevns" id="5$pbNpU3y1n" role="p0ZFr" />
              </node>
              <node concept="p0ZFY" id="5$pbNpU3y1o" role="p0ZFe">
                <ref role="peofi" node="5$pbNpU3y04" />
              </node>
            </node>
          </node>
        </node>
      </node>
      <node concept="p1rTg" id="5$pbNpU3y1p" role="p1kZ5" />
      <node concept="pevnv" id="5$pbNpU3y1q" role="peofa" />
    </node>
  </node>
  <node concept="pevno" id="5$pbNpU3y1r">
    <property role="TrG5h" value="float_group_sum_launch" />
    <ref role="pevni" node="5$pbNpU3xZK" />
    <node concept="pevnr" id="5$pbNpU3y1w" role="pevng">
      <property role="pevnc" value="input" />
      <property role="TrG5h" value="pixels" />
      <ref role="paaHl" node="5$pbNpU3y1D" />
      <node concept="wDpUf" id="5$pbNpU3y1y" role="pevnf" />
    </node>
    <node concept="pevnr" id="5$pbNpU3y1z" role="pevng">
      <property role="pevnc" value="input" />
      <property role="TrG5h" value="mask" />
      <ref role="paaHl" node="5$pbNpU3y1D" />
      <node concept="p1rTi" id="5$pbNpU3y1_" role="pevnf" />
    </node>
    <node concept="pevnr" id="5$pbNpU3y1A" role="pevng">
      <property role="pevnc" value="output" />
      <property role="TrG5h" value="output" />
      <ref role="paaHl" node="5$pbNpU3y1D" />
      <node concept="wDpUc" id="5$pbNpU3y1C" role="pevnf" />
    </node>
    <node concept="pevnr" id="5$pbNpU3y1D" role="pevng">
      <property role="pevnc" value="shape" />
      <property role="TrG5h" value="count" />
      <node concept="p1rTf" id="5$pbNpU3y1F" role="pevnf" />
    </node>
    <node concept="pevnq" id="5$pbNpU3y1G" role="pevnj">
      <property role="pevn9" value="64" />
      <property role="pevn8" value="true" />
      <ref role="pevna" node="5$pbNpU3xZO" />
      <ref role="pevn5" node="5$pbNpU3y1D" />
      <node concept="pevnl" id="5$pbNpU3y1I" role="pevnb">
        <ref role="pevn4" node="5$pbNpU3xZR" />
        <ref role="pevn7" node="5$pbNpU3y1w" />
      </node>
      <node concept="pevnl" id="5$pbNpU3y1J" role="pevnb">
        <ref role="pevn4" node="5$pbNpU3xZU" />
        <ref role="pevn7" node="5$pbNpU3y1z" />
      </node>
      <node concept="pevnl" id="5$pbNpU3y1K" role="pevnb">
        <ref role="pevn4" node="5$pbNpU3xZX" />
        <ref role="pevn7" node="5$pbNpU3y1A" />
      </node>
      <node concept="pevnl" id="5$pbNpU3y1L" role="pevnb">
        <ref role="pevn4" node="5$pbNpU3y00" />
        <ref role="pevn7" node="5$pbNpU3y1D" />
      </node>
    </node>
  </node>
</model>


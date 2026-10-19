<?xml version="1.0" encoding="UTF-8"?>
<model ref="r:05d92e52-e885-4044-b352-f69458660ba6(qpel.structure)">
  <persistence version="9" />
  <languages>
    <devkit ref="78434eb8-b0e5-444b-850d-e7c4ad2da9ab(jetbrains.mps.devkit.aspect.structure)" />
  </languages>
  <imports>
    <import index="tpck" ref="r:00000000-0000-4000-0000-011c89590288(jetbrains.mps.lang.core.structure)" implicit="true" />
  </imports>
  <registry>
    <language id="c72da2b9-7cce-4447-8389-f407dc1158b7" name="jetbrains.mps.lang.structure">
      <concept id="1169125787135" name="jetbrains.mps.lang.structure.structure.AbstractConceptDeclaration" flags="ig" index="PkWjJ">
        <property id="6714410169261853888" name="conceptId" index="EcuMT" />
        <property id="4628067390765956802" name="abstract" index="R5$K7" />
        <property id="5092175715804935370" name="conceptAlias" index="34LRSv" />
        <child id="1071489727083" name="linkDeclaration" index="1TKVEi" />
        <child id="1071489727084" name="propertyDeclaration" index="1TKVEl" />
      </concept>
      <concept id="1169127622168" name="jetbrains.mps.lang.structure.structure.InterfaceConceptReference" flags="ig" index="PrWs8">
        <reference id="1169127628841" name="intfc" index="PrY4T" />
      </concept>
      <concept id="1071489090640" name="jetbrains.mps.lang.structure.structure.ConceptDeclaration" flags="ig" index="1TIwiD">
        <property id="1096454100552" name="rootable" index="19KtqR" />
        <reference id="1071489389519" name="extends" index="1TJDcQ" />
        <child id="1169129564478" name="implements" index="PzmwI" />
      </concept>
      <concept id="1071489288299" name="jetbrains.mps.lang.structure.structure.PropertyDeclaration" flags="ig" index="1TJgyi">
        <property id="241647608299431129" name="propertyId" index="IQ2nx" />
        <reference id="1082985295845" name="dataType" index="AX2Wp" />
      </concept>
      <concept id="1071489288298" name="jetbrains.mps.lang.structure.structure.LinkDeclaration" flags="ig" index="1TJgyj">
        <property id="1071599776563" name="role" index="20kJfa" />
        <property id="1071599893252" name="sourceCardinality" index="20lbJX" />
        <property id="1071599937831" name="metaClass" index="20lmBu" />
        <property id="241647608299431140" name="linkId" index="IQ2ns" />
        <reference id="1071599976176" name="target" index="20lvS9" />
      </concept>
    </language>
    <language id="ceab5195-25ea-4f22-9b92-103b95ca8c0c" name="jetbrains.mps.lang.core">
      <concept id="1169194658468" name="jetbrains.mps.lang.core.structure.INamedConcept" flags="ngI" index="TrEIO">
        <property id="1169194664001" name="name" index="TrG5h" />
      </concept>
    </language>
  </registry>
  <node concept="1TIwiD" id="BpL3OgQzUY">
    <property role="EcuMT" value="709814182842351294" />
    <property role="TrG5h" value="GpuModule" />
    <property role="34LRSv" value="GpuModule" />
    <property role="19KtqR" value="true" />
    <ref role="1TJDcQ" to="tpck:gw2VY9q" />
    <node concept="1TJgyj" id="BpL3OgQzVi" role="1TKVEi">
      <property role="IQ2ns" value="709814182842351314" />
      <property role="20kJfa" value="functions" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj6/_1__n" />
      <ref role="20lvS9" node="BpL3OgQzUZ" resolve="Function" />
    </node>
    <node concept="PrWs8" id="BpL3OgQzVj" role="PzmwI">
      <ref role="PrY4T" to="tpck:h0TrEE$" resolve="INamedConcept" />
    </node>
  </node>
  <node concept="1TIwiD" id="BpL3OgQzUZ">
    <property role="EcuMT" value="709814182842351295" />
    <property role="TrG5h" value="Function" />
    <property role="34LRSv" value="Function" />
    <ref role="1TJDcQ" to="tpck:gw2VY9q" />
    <node concept="1TJgyi" id="BpL3OgQzVk" role="1TKVEl">
      <property role="IQ2nx" value="709814182842351316" />
      <property role="TrG5h" value="isKernel" />
      <ref role="AX2Wp" to="tpck:fKAQMTB" resolve="boolean" />
    </node>
    <node concept="1TJgyj" id="BpL3OgQzVm" role="1TKVEi">
      <property role="IQ2ns" value="709814182842351318" />
      <property role="20kJfa" value="parameters" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj5/_0__n" />
      <ref role="20lvS9" node="BpL3OgQzV0" resolve="Parameter" />
    </node>
    <node concept="1TJgyj" id="BpL3OgQzVn" role="1TKVEi">
      <property role="IQ2ns" value="709814182842351319" />
      <property role="20kJfa" value="body" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzVb" resolve="Block" />
    </node>
    <node concept="PrWs8" id="BpL3OgQzVo" role="PzmwI">
      <ref role="PrY4T" to="tpck:h0TrEE$" resolve="INamedConcept" />
    </node>
    <node concept="1TJgyj" id="BpL3OgR8JS" role="1TKVEi">
      <property role="IQ2ns" value="709814182842502136" />
      <property role="20kJfa" value="resultType" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgR7DG" resolve="GpuType" />
    </node>
    <node concept="1TJgyj" id="BpL3OgS4vR" role="1TKVEi">
      <property role="IQ2ns" value="709814182842746871" />
      <property role="20kJfa" value="domain" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <ref role="20lvS9" node="BpL3OgS37y" resolve="Grid1D" />
    </node>
  </node>
  <node concept="1TIwiD" id="BpL3OgQzV0">
    <property role="EcuMT" value="709814182842351296" />
    <property role="TrG5h" value="Parameter" />
    <property role="34LRSv" value="Parameter" />
    <ref role="1TJDcQ" to="tpck:gw2VY9q" />
    <node concept="1TJgyi" id="BpL3OgQzVq" role="1TKVEl">
      <property role="IQ2nx" value="709814182842351322" />
      <property role="TrG5h" value="binding" />
      <ref role="AX2Wp" to="tpck:fKAQMTA" resolve="integer" />
    </node>
    <node concept="1TJgyi" id="BpL3OgQzVs" role="1TKVEl">
      <property role="IQ2nx" value="709814182842351324" />
      <property role="TrG5h" value="isBuffer" />
      <ref role="AX2Wp" to="tpck:fKAQMTB" resolve="boolean" />
    </node>
    <node concept="PrWs8" id="BpL3OgQzVt" role="PzmwI">
      <ref role="PrY4T" to="tpck:h0TrEE$" resolve="INamedConcept" />
    </node>
    <node concept="1TJgyj" id="BpL3OgR8JW" role="1TKVEi">
      <property role="IQ2ns" value="709814182842502140" />
      <property role="20kJfa" value="type" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgR7DG" resolve="GpuType" />
    </node>
  </node>
  <node concept="1TIwiD" id="BpL3OgQzV1">
    <property role="EcuMT" value="709814182842351297" />
    <property role="TrG5h" value="Expression" />
    <property role="34LRSv" value="Expression" />
    <property role="R5$K7" value="true" />
    <ref role="1TJDcQ" to="tpck:gw2VY9q" />
  </node>
  <node concept="1TIwiD" id="BpL3OgQzV2">
    <property role="EcuMT" value="709814182842351298" />
    <property role="TrG5h" value="Statement" />
    <property role="34LRSv" value="Statement" />
    <property role="R5$K7" value="true" />
    <ref role="1TJDcQ" to="tpck:gw2VY9q" />
  </node>
  <node concept="1TIwiD" id="BpL3OgQzV3">
    <property role="EcuMT" value="709814182842351299" />
    <property role="TrG5h" value="Name" />
    <property role="34LRSv" value="Name" />
    <ref role="1TJDcQ" node="BpL3OgQzV1" resolve="Expression" />
    <node concept="1TJgyj" id="BpL3OgS4vJ" role="1TKVEi">
      <property role="IQ2ns" value="709814182842746863" />
      <property role="20kJfa" value="declaration" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" to="tpck:h0TrEE$" resolve="INamedConcept" />
    </node>
  </node>
  <node concept="1TIwiD" id="BpL3OgQzV4">
    <property role="EcuMT" value="709814182842351300" />
    <property role="TrG5h" value="Literal" />
    <property role="34LRSv" value="Literal" />
    <ref role="1TJDcQ" node="BpL3OgQzV1" resolve="Expression" />
    <node concept="1TJgyi" id="BpL3OgQzVv" role="1TKVEl">
      <property role="IQ2nx" value="709814182842351327" />
      <property role="TrG5h" value="value" />
      <ref role="AX2Wp" to="tpck:fKAOsGN" resolve="string" />
    </node>
  </node>
  <node concept="1TIwiD" id="BpL3OgQzV5">
    <property role="EcuMT" value="709814182842351301" />
    <property role="TrG5h" value="Binary" />
    <property role="34LRSv" value="Binary" />
    <ref role="1TJDcQ" node="BpL3OgQzV1" resolve="Expression" />
    <node concept="1TJgyi" id="BpL3OgQzVw" role="1TKVEl">
      <property role="IQ2nx" value="709814182842351328" />
      <property role="TrG5h" value="operator" />
      <ref role="AX2Wp" to="tpck:fKAOsGN" resolve="string" />
    </node>
    <node concept="1TJgyj" id="BpL3OgQzVx" role="1TKVEi">
      <property role="IQ2ns" value="709814182842351329" />
      <property role="20kJfa" value="left" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
    <node concept="1TJgyj" id="BpL3OgQzVy" role="1TKVEi">
      <property role="IQ2ns" value="709814182842351330" />
      <property role="20kJfa" value="right" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
  </node>
  <node concept="1TIwiD" id="BpL3OgQzV6">
    <property role="EcuMT" value="709814182842351302" />
    <property role="TrG5h" value="Unary" />
    <property role="34LRSv" value="Unary" />
    <ref role="1TJDcQ" node="BpL3OgQzV1" resolve="Expression" />
    <node concept="1TJgyi" id="BpL3OgQzVz" role="1TKVEl">
      <property role="IQ2nx" value="709814182842351331" />
      <property role="TrG5h" value="operator" />
      <ref role="AX2Wp" to="tpck:fKAOsGN" resolve="string" />
    </node>
    <node concept="1TJgyj" id="BpL3OgQzV$" role="1TKVEi">
      <property role="IQ2ns" value="709814182842351332" />
      <property role="20kJfa" value="operand" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
  </node>
  <node concept="1TIwiD" id="BpL3OgQzV7">
    <property role="EcuMT" value="709814182842351303" />
    <property role="TrG5h" value="Index" />
    <property role="34LRSv" value="Index" />
    <ref role="1TJDcQ" node="BpL3OgQzV1" resolve="Expression" />
    <node concept="1TJgyj" id="BpL3OgQzV_" role="1TKVEi">
      <property role="IQ2ns" value="709814182842351333" />
      <property role="20kJfa" value="base" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
    <node concept="1TJgyj" id="BpL3OgQzVA" role="1TKVEi">
      <property role="IQ2ns" value="709814182842351334" />
      <property role="20kJfa" value="index" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
  </node>
  <node concept="1TIwiD" id="BpL3OgQzV8">
    <property role="EcuMT" value="709814182842351304" />
    <property role="TrG5h" value="Member" />
    <property role="34LRSv" value="Member" />
    <ref role="1TJDcQ" node="BpL3OgQzV1" resolve="Expression" />
    <node concept="1TJgyi" id="BpL3OgQzVB" role="1TKVEl">
      <property role="IQ2nx" value="709814182842351335" />
      <property role="TrG5h" value="member" />
      <ref role="AX2Wp" to="tpck:fKAOsGN" resolve="string" />
    </node>
    <node concept="1TJgyj" id="BpL3OgQzVC" role="1TKVEi">
      <property role="IQ2ns" value="709814182842351336" />
      <property role="20kJfa" value="base" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
  </node>
  <node concept="1TIwiD" id="BpL3OgQzV9">
    <property role="EcuMT" value="709814182842351305" />
    <property role="TrG5h" value="Call" />
    <property role="34LRSv" value="Call" />
    <ref role="1TJDcQ" node="BpL3OgQzV1" resolve="Expression" />
    <node concept="1TJgyj" id="BpL3OgQzVE" role="1TKVEi">
      <property role="IQ2ns" value="709814182842351338" />
      <property role="20kJfa" value="arguments" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj5/_0__n" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
    <node concept="1TJgyj" id="BpL3OgS4vN" role="1TKVEi">
      <property role="IQ2ns" value="709814182842746867" />
      <property role="20kJfa" value="function" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzUZ" resolve="Function" />
    </node>
  </node>
  <node concept="1TIwiD" id="BpL3OgQzVa">
    <property role="EcuMT" value="709814182842351306" />
    <property role="TrG5h" value="Select" />
    <property role="34LRSv" value="Select" />
    <ref role="1TJDcQ" node="BpL3OgQzV1" resolve="Expression" />
    <node concept="1TJgyj" id="BpL3OgQzVF" role="1TKVEi">
      <property role="IQ2ns" value="709814182842351339" />
      <property role="20kJfa" value="condition" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
    <node concept="1TJgyj" id="BpL3OgQzVG" role="1TKVEi">
      <property role="IQ2ns" value="709814182842351340" />
      <property role="20kJfa" value="yes" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
    <node concept="1TJgyj" id="BpL3OgQzVH" role="1TKVEi">
      <property role="IQ2ns" value="709814182842351341" />
      <property role="20kJfa" value="no" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
  </node>
  <node concept="1TIwiD" id="BpL3OgQzVb">
    <property role="EcuMT" value="709814182842351307" />
    <property role="TrG5h" value="Block" />
    <property role="34LRSv" value="Block" />
    <ref role="1TJDcQ" node="BpL3OgQzV2" resolve="Statement" />
    <node concept="1TJgyj" id="BpL3OgQzVI" role="1TKVEi">
      <property role="IQ2ns" value="709814182842351342" />
      <property role="20kJfa" value="statements" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj5/_0__n" />
      <ref role="20lvS9" node="BpL3OgQzV2" resolve="Statement" />
    </node>
  </node>
  <node concept="1TIwiD" id="BpL3OgQzVc">
    <property role="EcuMT" value="709814182842351308" />
    <property role="TrG5h" value="Variable" />
    <property role="34LRSv" value="Variable" />
    <ref role="1TJDcQ" node="BpL3OgQzV2" resolve="Statement" />
    <node concept="1TJgyj" id="BpL3OgQzVK" role="1TKVEi">
      <property role="IQ2ns" value="709814182842351344" />
      <property role="20kJfa" value="value" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
    <node concept="PrWs8" id="BpL3OgQzVL" role="PzmwI">
      <ref role="PrY4T" to="tpck:h0TrEE$" resolve="INamedConcept" />
    </node>
    <node concept="1TJgyj" id="BpL3OgR8K0" role="1TKVEi">
      <property role="IQ2ns" value="709814182842502144" />
      <property role="20kJfa" value="type" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgR7DG" resolve="GpuType" />
    </node>
  </node>
  <node concept="1TIwiD" id="BpL3OgQzVd">
    <property role="EcuMT" value="709814182842351309" />
    <property role="TrG5h" value="Assign" />
    <property role="34LRSv" value="Assign" />
    <ref role="1TJDcQ" node="BpL3OgQzV2" resolve="Statement" />
    <node concept="1TJgyj" id="BpL3OgQzVM" role="1TKVEi">
      <property role="IQ2ns" value="709814182842351346" />
      <property role="20kJfa" value="target" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
    <node concept="1TJgyj" id="BpL3OgQzVN" role="1TKVEi">
      <property role="IQ2ns" value="709814182842351347" />
      <property role="20kJfa" value="value" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
  </node>
  <node concept="1TIwiD" id="BpL3OgQzVe">
    <property role="EcuMT" value="709814182842351310" />
    <property role="TrG5h" value="Evaluate" />
    <property role="34LRSv" value="Evaluate" />
    <ref role="1TJDcQ" node="BpL3OgQzV2" resolve="Statement" />
    <node concept="1TJgyj" id="BpL3OgQzVO" role="1TKVEi">
      <property role="IQ2ns" value="709814182842351348" />
      <property role="20kJfa" value="value" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
  </node>
  <node concept="1TIwiD" id="BpL3OgQzVf">
    <property role="EcuMT" value="709814182842351311" />
    <property role="TrG5h" value="If" />
    <property role="34LRSv" value="If" />
    <ref role="1TJDcQ" node="BpL3OgQzV2" resolve="Statement" />
    <node concept="1TJgyj" id="BpL3OgQzVP" role="1TKVEi">
      <property role="IQ2ns" value="709814182842351349" />
      <property role="20kJfa" value="condition" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
    <node concept="1TJgyj" id="BpL3OgQzVQ" role="1TKVEi">
      <property role="IQ2ns" value="709814182842351350" />
      <property role="20kJfa" value="thenBody" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzVb" resolve="Block" />
    </node>
  </node>
  <node concept="1TIwiD" id="BpL3OgQzVg">
    <property role="EcuMT" value="709814182842351312" />
    <property role="TrG5h" value="While" />
    <property role="34LRSv" value="While" />
    <ref role="1TJDcQ" node="BpL3OgQzV2" resolve="Statement" />
    <node concept="1TJgyj" id="BpL3OgQzVR" role="1TKVEi">
      <property role="IQ2ns" value="709814182842351351" />
      <property role="20kJfa" value="condition" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
    <node concept="1TJgyj" id="BpL3OgQzVS" role="1TKVEi">
      <property role="IQ2ns" value="709814182842351352" />
      <property role="20kJfa" value="body" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzVb" resolve="Block" />
    </node>
  </node>
  <node concept="1TIwiD" id="BpL3OgQzVh">
    <property role="EcuMT" value="709814182842351313" />
    <property role="TrG5h" value="Return" />
    <property role="34LRSv" value="Return" />
    <ref role="1TJDcQ" node="BpL3OgQzV2" resolve="Statement" />
    <node concept="1TJgyj" id="BpL3OgQzVT" role="1TKVEi">
      <property role="IQ2ns" value="709814182842351353" />
      <property role="20kJfa" value="value" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
  </node>
  <node concept="1TIwiD" id="BpL3OgR7DG">
    <property role="EcuMT" value="709814182842497644" />
    <property role="TrG5h" value="GpuType" />
    <property role="34LRSv" value="GpuType" />
    <property role="R5$K7" value="true" />
    <ref role="1TJDcQ" to="tpck:gw2VY9q" />
  </node>
  <node concept="1TIwiD" id="BpL3OgR7DH">
    <property role="EcuMT" value="709814182842497645" />
    <property role="TrG5h" value="VoidType" />
    <property role="34LRSv" value="VoidType" />
    <ref role="1TJDcQ" node="BpL3OgR7DG" resolve="GpuType" />
  </node>
  <node concept="1TIwiD" id="BpL3OgR7DI">
    <property role="EcuMT" value="709814182842497646" />
    <property role="TrG5h" value="U32Type" />
    <property role="34LRSv" value="U32Type" />
    <ref role="1TJDcQ" node="BpL3OgR7DG" resolve="GpuType" />
  </node>
  <node concept="1TIwiD" id="BpL3OgR7DJ">
    <property role="EcuMT" value="709814182842497647" />
    <property role="TrG5h" value="ReadOnlyU8BufferType" />
    <property role="34LRSv" value="ReadOnlyU8BufferType" />
    <ref role="1TJDcQ" node="BpL3OgR7DG" resolve="GpuType" />
  </node>
  <node concept="1TIwiD" id="BpL3OgR7DK">
    <property role="EcuMT" value="709814182842497648" />
    <property role="TrG5h" value="AtomicU32BufferType" />
    <property role="34LRSv" value="AtomicU32BufferType" />
    <ref role="1TJDcQ" node="BpL3OgR7DG" resolve="GpuType" />
  </node>
  <node concept="1TIwiD" id="BpL3OgR7DL">
    <property role="EcuMT" value="709814182842497649" />
    <property role="TrG5h" value="U32BufferType" />
    <property role="34LRSv" value="U32BufferType" />
    <ref role="1TJDcQ" node="BpL3OgR7DG" resolve="GpuType" />
  </node>
  <node concept="1TIwiD" id="BpL3OgR7DM">
    <property role="EcuMT" value="709814182842497650" />
    <property role="TrG5h" value="Shape3DType" />
    <property role="34LRSv" value="Shape3DType" />
    <ref role="1TJDcQ" node="BpL3OgR7DG" resolve="GpuType" />
  </node>
  <node concept="1TIwiD" id="BpL3OgR7DN">
    <property role="EcuMT" value="709814182842497651" />
    <property role="TrG5h" value="AtomicLoad" />
    <property role="34LRSv" value="AtomicLoad" />
    <ref role="1TJDcQ" node="BpL3OgQzV1" resolve="Expression" />
    <node concept="1TJgyj" id="BpL3OgR7DR" role="1TKVEi">
      <property role="IQ2ns" value="709814182842497655" />
      <property role="20kJfa" value="address" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
  </node>
  <node concept="1TIwiD" id="BpL3OgR7DO">
    <property role="EcuMT" value="709814182842497652" />
    <property role="TrG5h" value="AtomicStore" />
    <property role="34LRSv" value="AtomicStore" />
    <ref role="1TJDcQ" node="BpL3OgQzV1" resolve="Expression" />
    <node concept="1TJgyj" id="BpL3OgR7DS" role="1TKVEi">
      <property role="IQ2ns" value="709814182842497656" />
      <property role="20kJfa" value="address" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
    <node concept="1TJgyj" id="BpL3OgR7DT" role="1TKVEi">
      <property role="IQ2ns" value="709814182842497657" />
      <property role="20kJfa" value="value" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
  </node>
  <node concept="1TIwiD" id="BpL3OgR7DP">
    <property role="EcuMT" value="709814182842497653" />
    <property role="TrG5h" value="AtomicAdd" />
    <property role="34LRSv" value="AtomicAdd" />
    <ref role="1TJDcQ" node="BpL3OgQzV1" resolve="Expression" />
    <node concept="1TJgyj" id="BpL3OgR7DU" role="1TKVEi">
      <property role="IQ2ns" value="709814182842497658" />
      <property role="20kJfa" value="address" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
    <node concept="1TJgyj" id="BpL3OgR7DV" role="1TKVEi">
      <property role="IQ2ns" value="709814182842497659" />
      <property role="20kJfa" value="value" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
  </node>
  <node concept="1TIwiD" id="BpL3OgR7DQ">
    <property role="EcuMT" value="709814182842497654" />
    <property role="TrG5h" value="AtomicMin" />
    <property role="34LRSv" value="AtomicMin" />
    <ref role="1TJDcQ" node="BpL3OgQzV1" resolve="Expression" />
    <node concept="1TJgyj" id="BpL3OgR7DW" role="1TKVEi">
      <property role="IQ2ns" value="709814182842497660" />
      <property role="20kJfa" value="address" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
    <node concept="1TJgyj" id="BpL3OgR7DX" role="1TKVEi">
      <property role="IQ2ns" value="709814182842497661" />
      <property role="20kJfa" value="value" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
  </node>
  <node concept="1TIwiD" id="BpL3OgS37v">
    <property role="EcuMT" value="709814182842741215" />
    <property role="TrG5h" value="BoolType" />
    <property role="34LRSv" value="BoolType" />
    <ref role="1TJDcQ" node="BpL3OgR7DG" resolve="GpuType" />
  </node>
  <node concept="1TIwiD" id="BpL3OgS37w">
    <property role="EcuMT" value="709814182842741216" />
    <property role="TrG5h" value="U8Type" />
    <property role="34LRSv" value="U8Type" />
    <ref role="1TJDcQ" node="BpL3OgR7DG" resolve="GpuType" />
  </node>
  <node concept="1TIwiD" id="BpL3OgS37x">
    <property role="EcuMT" value="709814182842741217" />
    <property role="TrG5h" value="GridIndex" />
    <property role="34LRSv" value="GridIndex" />
    <ref role="1TJDcQ" node="BpL3OgQzV1" resolve="Expression" />
  </node>
  <node concept="1TIwiD" id="BpL3OgS37y">
    <property role="EcuMT" value="709814182842741218" />
    <property role="TrG5h" value="Grid1D" />
    <property role="34LRSv" value="Grid1D" />
    <ref role="1TJDcQ" to="tpck:gw2VY9q" />
  </node>
  <node concept="1TIwiD" id="BpL3OgS37z">
    <property role="EcuMT" value="709814182842741219" />
    <property role="TrG5h" value="Minimum" />
    <property role="34LRSv" value="Minimum" />
    <ref role="1TJDcQ" node="BpL3OgQzV1" resolve="Expression" />
    <node concept="1TJgyj" id="BpL3OgS37D" role="1TKVEi">
      <property role="IQ2ns" value="709814182842741225" />
      <property role="20kJfa" value="left" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
    <node concept="1TJgyj" id="BpL3OgS37E" role="1TKVEi">
      <property role="IQ2ns" value="709814182842741226" />
      <property role="20kJfa" value="right" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
  </node>
  <node concept="1TIwiD" id="BpL3OgS37$">
    <property role="EcuMT" value="709814182842741220" />
    <property role="TrG5h" value="Maximum" />
    <property role="34LRSv" value="Maximum" />
    <ref role="1TJDcQ" node="BpL3OgQzV1" resolve="Expression" />
    <node concept="1TJgyj" id="BpL3OgS37F" role="1TKVEi">
      <property role="IQ2ns" value="709814182842741227" />
      <property role="20kJfa" value="left" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
    <node concept="1TJgyj" id="BpL3OgS37G" role="1TKVEi">
      <property role="IQ2ns" value="709814182842741228" />
      <property role="20kJfa" value="right" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
  </node>
  <node concept="1TIwiD" id="BpL3OgS37_">
    <property role="EcuMT" value="709814182842741221" />
    <property role="TrG5h" value="Pipeline" />
    <property role="34LRSv" value="Pipeline" />
    <property role="19KtqR" value="true" />
    <ref role="1TJDcQ" to="tpck:gw2VY9q" />
    <node concept="1TJgyj" id="BpL3OgS37H" role="1TKVEi">
      <property role="IQ2ns" value="709814182842741229" />
      <property role="20kJfa" value="resources" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj6/_1__n" />
      <ref role="20lvS9" node="BpL3OgS37A" resolve="Resource" />
    </node>
    <node concept="1TJgyj" id="BpL3OgS37I" role="1TKVEi">
      <property role="IQ2ns" value="709814182842741230" />
      <property role="20kJfa" value="stages" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj6/_1__n" />
      <ref role="20lvS9" node="BpL3OgS37B" resolve="Dispatch" />
    </node>
    <node concept="1TJgyj" id="BpL3OgS37J" role="1TKVEi">
      <property role="IQ2ns" value="709814182842741231" />
      <property role="20kJfa" value="module" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzUY" resolve="GpuModule" />
    </node>
    <node concept="PrWs8" id="BpL3OgS37K" role="PzmwI">
      <ref role="PrY4T" to="tpck:h0TrEE$" resolve="INamedConcept" />
    </node>
  </node>
  <node concept="1TIwiD" id="BpL3OgS37A">
    <property role="EcuMT" value="709814182842741222" />
    <property role="TrG5h" value="Resource" />
    <property role="34LRSv" value="Resource" />
    <ref role="1TJDcQ" to="tpck:gw2VY9q" />
    <node concept="1TJgyi" id="BpL3OgS37L" role="1TKVEl">
      <property role="IQ2nx" value="709814182842741233" />
      <property role="TrG5h" value="kind" />
      <ref role="AX2Wp" to="tpck:fKAOsGN" resolve="string" />
    </node>
    <node concept="1TJgyj" id="BpL3OgS37M" role="1TKVEi">
      <property role="IQ2ns" value="709814182842741234" />
      <property role="20kJfa" value="type" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgR7DG" resolve="GpuType" />
    </node>
    <node concept="PrWs8" id="BpL3OgS37N" role="PzmwI">
      <ref role="PrY4T" to="tpck:h0TrEE$" resolve="INamedConcept" />
    </node>
    <node concept="1TJgyj" id="BpL3OgWmXC" role="1TKVEi">
      <property role="IQ2ns" value="709814182843871080" />
      <property role="20kJfa" value="extent" />
      <ref role="20lvS9" node="BpL3OgS37A" resolve="Resource" />
    </node>
  </node>
  <node concept="1TIwiD" id="BpL3OgS37B">
    <property role="EcuMT" value="709814182842741223" />
    <property role="TrG5h" value="Dispatch" />
    <property role="34LRSv" value="Dispatch" />
    <ref role="1TJDcQ" to="tpck:gw2VY9q" />
    <node concept="1TJgyi" id="BpL3OgS37O" role="1TKVEl">
      <property role="IQ2nx" value="709814182842741236" />
      <property role="TrG5h" value="blockSize" />
      <ref role="AX2Wp" to="tpck:fKAQMTA" resolve="integer" />
    </node>
    <node concept="1TJgyi" id="BpL3OgS37P" role="1TKVEl">
      <property role="IQ2nx" value="709814182842741237" />
      <property role="TrG5h" value="completionBarrier" />
      <ref role="AX2Wp" to="tpck:fKAQMTB" resolve="boolean" />
    </node>
    <node concept="1TJgyj" id="BpL3OgS37Q" role="1TKVEi">
      <property role="IQ2ns" value="709814182842741238" />
      <property role="20kJfa" value="bindings" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj6/_1__n" />
      <ref role="20lvS9" node="BpL3OgS37C" resolve="Binding" />
    </node>
    <node concept="1TJgyj" id="BpL3OgS37R" role="1TKVEi">
      <property role="IQ2ns" value="709814182842741239" />
      <property role="20kJfa" value="kernel" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzUZ" resolve="Function" />
    </node>
    <node concept="1TJgyj" id="BpL3OgS37S" role="1TKVEi">
      <property role="IQ2ns" value="709814182842741240" />
      <property role="20kJfa" value="grid" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgS37A" resolve="Resource" />
    </node>
  </node>
  <node concept="1TIwiD" id="BpL3OgS37C">
    <property role="EcuMT" value="709814182842741224" />
    <property role="TrG5h" value="Binding" />
    <property role="34LRSv" value="Binding" />
    <ref role="1TJDcQ" to="tpck:gw2VY9q" />
    <node concept="1TJgyj" id="BpL3OgS37T" role="1TKVEi">
      <property role="IQ2ns" value="709814182842741241" />
      <property role="20kJfa" value="parameter" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV0" resolve="Parameter" />
    </node>
    <node concept="1TJgyj" id="BpL3OgS37U" role="1TKVEi">
      <property role="IQ2ns" value="709814182842741242" />
      <property role="20kJfa" value="resource" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgS37A" resolve="Resource" />
    </node>
  </node>
  <node concept="1TIwiD" id="5$pbNpTJb6S">
    <property role="EcuMT" value="6420214654810501560" />
    <property role="TrG5h" value="F32Type" />
    <property role="34LRSv" value="F32Type" />
    <ref role="1TJDcQ" node="BpL3OgR7DG" resolve="GpuType" />
  </node>
  <node concept="1TIwiD" id="5$pbNpTJb6T">
    <property role="EcuMT" value="6420214654810501561" />
    <property role="TrG5h" value="ReadOnlyF32BufferType" />
    <property role="34LRSv" value="ReadOnlyF32BufferType" />
    <ref role="1TJDcQ" node="BpL3OgR7DG" resolve="GpuType" />
  </node>
  <node concept="1TIwiD" id="5$pbNpTJb6U">
    <property role="EcuMT" value="6420214654810501562" />
    <property role="TrG5h" value="F32BufferType" />
    <property role="34LRSv" value="F32BufferType" />
    <ref role="1TJDcQ" node="BpL3OgR7DG" resolve="GpuType" />
  </node>
  <node concept="1TIwiD" id="5$pbNpTJb6V">
    <property role="EcuMT" value="6420214654810501563" />
    <property role="TrG5h" value="F32Literal" />
    <property role="34LRSv" value="F32Literal" />
    <ref role="1TJDcQ" node="BpL3OgQzV1" resolve="Expression" />
    <node concept="1TJgyi" id="5$pbNpTJb6X" role="1TKVEl">
      <property role="IQ2nx" value="6420214654810501565" />
      <property role="TrG5h" value="value" />
      <ref role="AX2Wp" to="tpck:fKAOsGN" resolve="string" />
    </node>
  </node>
  <node concept="1TIwiD" id="5$pbNpTJb6W">
    <property role="EcuMT" value="6420214654810501564" />
    <property role="TrG5h" value="F32Cast" />
    <property role="34LRSv" value="F32Cast" />
    <ref role="1TJDcQ" node="BpL3OgQzV1" resolve="Expression" />
    <node concept="1TJgyj" id="5$pbNpTJb6Y" role="1TKVEi">
      <property role="IQ2ns" value="6420214654810501566" />
      <property role="20kJfa" value="operand" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
  </node>
  <node concept="1TIwiD" id="5$pbNpTP8S2">
    <property role="EcuMT" value="6420214654812065282" />
    <property role="TrG5h" value="F32Fma" />
    <property role="34LRSv" value="F32Fma" />
    <ref role="1TJDcQ" node="BpL3OgQzV1" resolve="Expression" />
    <node concept="1TJgyj" id="5$pbNpTP8S3" role="1TKVEi">
      <property role="IQ2ns" value="6420214654812065283" />
      <property role="20kJfa" value="left" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
    <node concept="1TJgyj" id="5$pbNpTP8S4" role="1TKVEi">
      <property role="IQ2ns" value="6420214654812065284" />
      <property role="20kJfa" value="right" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
    <node concept="1TJgyj" id="5$pbNpTP8S5" role="1TKVEi">
      <property role="IQ2ns" value="6420214654812065285" />
      <property role="20kJfa" value="addend" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
  </node>
  <node concept="1TIwiD" id="5$pbNpTRXFK">
    <property role="EcuMT" value="6420214654812805872" />
    <property role="TrG5h" value="F16Type" />
    <property role="34LRSv" value="F16Type" />
    <ref role="1TJDcQ" node="BpL3OgR7DG" resolve="GpuType" />
  </node>
  <node concept="1TIwiD" id="5$pbNpTRXFL">
    <property role="EcuMT" value="6420214654812805873" />
    <property role="TrG5h" value="ReadOnlyF16BufferType" />
    <property role="34LRSv" value="ReadOnlyF16BufferType" />
    <ref role="1TJDcQ" node="BpL3OgR7DG" resolve="GpuType" />
  </node>
  <node concept="1TIwiD" id="5$pbNpTRXFM">
    <property role="EcuMT" value="6420214654812805874" />
    <property role="TrG5h" value="F16BufferType" />
    <property role="34LRSv" value="F16BufferType" />
    <ref role="1TJDcQ" node="BpL3OgR7DG" resolve="GpuType" />
  </node>
  <node concept="1TIwiD" id="5$pbNpTRXFN">
    <property role="EcuMT" value="6420214654812805875" />
    <property role="TrG5h" value="F16Cast" />
    <property role="34LRSv" value="F16Cast" />
    <ref role="1TJDcQ" node="BpL3OgQzV1" resolve="Expression" />
    <node concept="1TJgyj" id="5$pbNpTRXFO" role="1TKVEi">
      <property role="IQ2ns" value="6420214654812805876" />
      <property role="20kJfa" value="operand" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
  </node>
  <node concept="1TIwiD" id="5$pbNpTX6ys">
    <property role="EcuMT" value="6420214654814152860" />
    <property role="TrG5h" value="ReduceSumF32" />
    <property role="34LRSv" value="ReduceSumF32" />
    <ref role="1TJDcQ" node="BpL3OgQzV1" resolve="Expression" />
    <node concept="1TJgyj" id="5$pbNpTX6yt" role="1TKVEi">
      <property role="IQ2ns" value="6420214654814152861" />
      <property role="20kJfa" value="buffer" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
    <node concept="1TJgyj" id="5$pbNpTX6yu" role="1TKVEi">
      <property role="IQ2ns" value="6420214654814152862" />
      <property role="20kJfa" value="start" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
    <node concept="1TJgyj" id="5$pbNpTX6yv" role="1TKVEi">
      <property role="IQ2ns" value="6420214654814152863" />
      <property role="20kJfa" value="count" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
  </node>
  <node concept="1TIwiD" id="5$pbNpU0A0d">
    <property role="EcuMT" value="6420214654815068173" />
    <property role="TrG5h" value="WorkgroupSumF32" />
    <property role="34LRSv" value="WorkgroupSumF32" />
    <ref role="1TJDcQ" node="BpL3OgQzV1" resolve="Expression" />
    <node concept="1TJgyj" id="5$pbNpU0A0e" role="1TKVEi">
      <property role="IQ2ns" value="6420214654815068174" />
      <property role="20kJfa" value="value" />
      <property role="20lmBu" value="fLJjDmT/aggregation" />
      <property role="20lbJX" value="fLJekj4/_1" />
      <ref role="20lvS9" node="BpL3OgQzV1" resolve="Expression" />
    </node>
  </node>
</model>


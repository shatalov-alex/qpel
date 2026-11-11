<?xml version="1.0" encoding="UTF-8"?>
<model ref="r:4a5c2df0-bc7d-44bd-afde-1f3aaffdef36(qpel.textGen)">
  <persistence version="9" />
  <languages>
    <use id="b83431fe-5c8f-40bc-8a36-65e25f4dd253" name="jetbrains.mps.lang.textGen" version="1" />
    <use id="413527eb-5550-4ad0-bc1b-796b7599a8f0" name="qpel" version="0" />
    <use id="f3061a53-9226-4cc5-a443-f952ceaf5816" name="jetbrains.mps.baseLanguage" version="12" />
    <use id="7866978e-a0f0-4cc7-81bc-4d213d9375e1" name="jetbrains.mps.lang.smodel" version="19" />
    <devkit ref="fa73d85a-ac7f-447b-846c-fcdc41caa600(jetbrains.mps.devkit.aspect.textgen)" />
  </languages>
  <imports>
    <import index="gf8" ref="r:05d92e52-e885-4044-b352-f69458660ba6(qpel.structure)" />
    <import index="tpck" ref="r:00000000-0000-4000-0000-011c89590288(jetbrains.mps.lang.core.structure)" />
    <import index="nk73" ref="r:277bc199-aba3-4846-ba2f-ac9aceac7dba(qpel.typesystem)" />
  </imports>
  <registry>
    <language id="f3061a53-9226-4cc5-a443-f952ceaf5816" name="jetbrains.mps.baseLanguage">
      <concept id="1137021947720" name="jetbrains.mps.baseLanguage.structure.ConceptFunction" flags="in" index="2VMwT0">
        <child id="1137022507850" name="body" index="2VODD2" />
      </concept>
      <concept id="1070475926800" name="jetbrains.mps.baseLanguage.structure.StringLiteral" flags="nn" index="Xl_RD">
        <property id="1070475926801" name="value" index="Xl_RC" />
      </concept>
      <concept id="1081236700937" name="jetbrains.mps.baseLanguage.structure.StaticMethodCall" flags="nn" index="2YIFZM">
        <reference id="1144433194310" name="classConcept" index="1Pybhc" />
      </concept>
      <concept id="1068580123159" name="jetbrains.mps.baseLanguage.structure.IfStatement" flags="nn" index="3clFbJ">
        <child id="1068580123160" name="condition" index="3clFbw" />
        <child id="1068580123161" name="ifTrue" index="3clFbx" />
      </concept>
      <concept id="1068580123136" name="jetbrains.mps.baseLanguage.structure.StatementList" flags="sn" stub="5293379017992965193" index="3clFbS">
        <child id="1068581517665" name="statement" index="3cqZAp" />
      </concept>
      <concept id="1068581242878" name="jetbrains.mps.baseLanguage.structure.ReturnStatement" flags="nn" index="3cpWs6">
        <child id="1068581517676" name="expression" index="3cqZAk" />
      </concept>
      <concept id="1204053956946" name="jetbrains.mps.baseLanguage.structure.IMethodCall" flags="ngI" index="1ndlxa">
        <reference id="1068499141037" name="baseMethodDeclaration" index="37wK5l" />
        <child id="1068499141038" name="actualArgument" index="37wK5m" />
      </concept>
    </language>
    <language id="b83431fe-5c8f-40bc-8a36-65e25f4dd253" name="jetbrains.mps.lang.textGen">
      <concept id="8931911391946696733" name="jetbrains.mps.lang.textGen.structure.ExtensionDeclaration" flags="in" index="9MYSb" />
      <concept id="1237305334312" name="jetbrains.mps.lang.textGen.structure.NodeAppendPart" flags="ng" index="l9hG8">
        <child id="1237305790512" name="value" index="lb14g" />
      </concept>
      <concept id="1237306079178" name="jetbrains.mps.lang.textGen.structure.AppendOperation" flags="nn" index="lc7rE">
        <child id="1237306115446" name="part" index="lcghm" />
      </concept>
      <concept id="1233670071145" name="jetbrains.mps.lang.textGen.structure.ConceptTextGenDeclaration" flags="ig" index="WtQ9Q">
        <reference id="1233670257997" name="conceptDeclaration" index="WuzLi" />
        <child id="1233749296504" name="textGenBlock" index="11c4hB" />
        <child id="7991274449437422201" name="extension" index="33IsuW" />
      </concept>
      <concept id="1233748055915" name="jetbrains.mps.lang.textGen.structure.NodeParameter" flags="nn" index="117lpO" />
      <concept id="1233749247888" name="jetbrains.mps.lang.textGen.structure.GenerateTextDeclaration" flags="in" index="11bSqf" />
      <concept id="1234794705341" name="jetbrains.mps.lang.textGen.structure.FoundErrorOperation" flags="nn" index="1ZvZ2y">
        <child id="1237470722868" name="text" index="v0bCk" />
      </concept>
    </language>
    <language id="ceab5195-25ea-4f22-9b92-103b95ca8c0c" name="jetbrains.mps.lang.core">
      <concept id="1169194658468" name="jetbrains.mps.lang.core.structure.INamedConcept" flags="ngI" index="TrEIO">
        <property id="1169194664001" name="name" index="TrG5h" />
      </concept>
    </language>
  </registry>
  <node concept="WtQ9Q" id="BpL3OgXop8">
    <property role="TrG5h" value="Pipeline_IR" />
    <ref role="WuzLi" to="gf8:BpL3OgS37_" resolve="Pipeline" />
    <node concept="11bSqf" id="BpL3OgXop9" role="11c4hB">
      <node concept="3clFbS" id="BpL3OgXopb" role="2VODD2">
        <node concept="3clFbJ" id="BpL3OgXopc" role="3cqZAp">
          <node concept="2YIFZM" id="BpL3OgXopf" role="3clFbw">
            <ref role="1Pybhc" to="nk73:BpL3OgSmp4" resolve="QpelChecks" />
            <ref role="37wK5l" to="nk73:BpL3OgWD0B" resolve="hasProblems" />
            <node concept="117lpO" id="BpL3OgXopg" role="37wK5m" />
          </node>
          <node concept="3clFbS" id="BpL3OgXoph" role="3clFbx">
            <node concept="1ZvZ2y" id="BpL3OgXopi" role="3cqZAp">
              <node concept="2YIFZM" id="BpL3OgXopj" role="v0bCk">
                <ref role="1Pybhc" to="nk73:BpL3OgSmp4" resolve="QpelChecks" />
                <ref role="37wK5l" to="nk73:BpL3OgVzRr" resolve="summary" />
                <node concept="117lpO" id="BpL3OgXopk" role="37wK5m" />
              </node>
            </node>
          </node>
        </node>
        <node concept="lc7rE" id="BpL3OgXopl" role="3cqZAp">
          <node concept="l9hG8" id="BpL3OgXopn" role="lcghm">
            <node concept="2YIFZM" id="BpL3OgXopp" role="lb14g">
              <ref role="1Pybhc" to="nk73:BpL3OgSmp4" resolve="QpelChecks" />
              <ref role="37wK5l" to="nk73:5$pbNpU6e$o" resolve="irJson" />
              <node concept="117lpO" id="BpL3OgXopq" role="37wK5m" />
            </node>
          </node>
        </node>
      </node>
    </node>
    <node concept="9MYSb" id="BpL3OgXopr" role="33IsuW">
      <node concept="3clFbS" id="BpL3OgXopt" role="2VODD2">
        <node concept="3cpWs6" id="BpL3OgXopu" role="3cqZAp">
          <node concept="Xl_RD" id="BpL3OgXopv" role="3cqZAk">
            <property role="Xl_RC" value="qpel.json" />
          </node>
        </node>
      </node>
    </node>
  </node>
</model>


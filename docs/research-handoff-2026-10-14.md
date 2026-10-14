# Hypertrix: teknisk status og første deltakerpilot

Til Yngvar. Utarbeidet 14. oktober 2026. Eksperimentell plattform, én operatør.

## Kort svar på det tekniske spørsmålet

Vi har nå en fullført finjustering av Qwen3-0.6B med **eksakt gjentakelse av en
hel epoke**. En ny prosess startet fra grunnmodellen og gjentok alle 228
optimizer-oppdateringene: **392 av 392 adaptertensorer var bit-identiske**, med
null maksimal forskjell og null relativ L2-avstand. Dette er den nye kjøringen
`finetune-strict-002`, med strengt deterministisk math-attention-policy.

Den tidligere tre-epokersmodellen er fortsatt kjørbar i Ollama. Dens opprinnelige
replay-feil er bevart. Den nye kjøringen har egen oppskrift, modellversjon og
signert historikk; den gjør ikke om på resultatene fra den gamle modellen.

Det finnes også et separat SmolLM2-forsøk som faktisk har fordelt trening og
verifisering mellom flere noder. Dette må ikke forveksles med Qwen-finjusteringen.

## Én fullført kjede: data til lokal modell

1. **Utgangspunkt:** Qwen/Qwen3-0.6B, publisert med Apache-2.0-lisens, revisjon
   `c1899de289a04d12100db370d81485cdf75e47ca`. Filhashene er registrert.
   Historien før dette sjekkpunktet er representert ved leverandørens dokumentasjon.
2. **Datagrunnlag:** 16 bøker, inkludert økonomitekster av Adam Smith, David Ricardo
   og Walter Bagehot. 11 016 386 byte originaltekst, 453 kildesegmenter og 4 427
   tokenvinduer. Bevarte originalfiler, kildeadresser, deklarerte rettigheter,
   rensegrenser og partisjonering er registrert. Partisjoneringen kan ikke avgjøre
   om grunnmodellen tidligere har sett de samme bøkene.
3. **Trening:** 3 epoker, 684 AdamW-oppdateringer, 5 601 792 neste-token-mål.
   LoRA med rang 16 og alpha 32 oppdaterte 10 092 544 parametere; grunnvektene
   var frosset. NVIDIA RTX 5090 Laptop GPU, CUDA 12.8, PyTorch 2.11.
   Oppskrift, datarekkefølge, programversjoner og sjekkpunkter er registrert.
4. **Utvalg og eksport:** epoke 3 ble valgt med en på forhånd fastsatt
   valideringsregel. LoRA ble slått sammen med grunnvektene og eksportert til BF16
   GGUF. Ollama serverer `hypertrix-economics-epoch-3:001`.
5. **Forbindelse:** 15 Ed25519-signerte poster binder innganger, resultater,
   sjekkpunkter og serveringspakken. Den opprinnelige pakken inneholder også ni
   faktisk genererte svar fra det tidligere modellforsøket. Yngvars nye seks
   rådstekster er forskerskrevne stimuli og inngår ikke som modellgenererte svar.

Testtapet (NLL) for økonomitekst falt fra 2,998768 til 2,424236; litteratur fra
3,713823 til 3,246297. Økonomispørsmålene gikk fra 8/12 til 7/12 riktige.
Dette er små, beskrivende målinger: lavere språktap viste ikke bedre oppgaveløsning.

## Hva en utenforstående kan kontrollere

**Med den offentlige dokumentpakken:** lese den spesifiserte oppskriften, kontrollere
signaturkjeden mot en separat innhentet offentlig nøkkel, sjekke hashene til de
medfølgende filene, undersøke resultatene og feilkvitteringene, og kontrollere
sertifikater fra det separate Oracle-forsøket mot en festet genesis-hash.
En hash som bare ligger ved siden av en fil gir integritetskontroll; tillit til
avsender krever at nøkkelen eller genesis-forankringen er kjent fra en annen kanal.

**Med originaltekstene, tokenizer og modellfilene i tillegg:** gjenskape alle
tokenvinduer, kontrollere modell- og adapterfiler, etterprøve sammenslåing og
eksportforbindelse, og kjøre treningsoppskriften i den dokumenterte programvaren.
De delbare ZIP-pakkene inneholder offentlig metadata, ikke hele korpuset eller
alle modellvektene. En annen forsker må derfor få disse separat eller laste ned
de festede kildene og sammenligne hashene. Nettadressene alene garanterer ikke at
samme fil fortsatt leveres.

**Observerte kontroller:** alle 4 427 tokenvinduer ble gjenskapt identisk;
15 signerte poster ble kontrollert; CPU-kontroll av sammenslåingen bestod for
310/310 eksporterte tensorer, inkludert 196 adapterte lineære avbildninger.
Kontroll av original treningsepoke 1 ga 0/392 identiske adaptertensorer,
relativ L2-avstand 0,00797163 og maksimal absolutt forskjell 0,00240201.
Dette var en mislykket eksakt kontroll, ikke en godkjenning med toleranse.

Den separate diagnosen fant avvik allerede i første bakoverpassering:
190/392 gradienttensorer var forskjellige med den opprinnelige automatiske
attention-banen. Math attention fjernet avviket i to firestegs kjøringer.
Streng deterministisk policy med math attention ga 392/392 identiske
adaptertensorer etter 12 oppdateringer per prosess. En hel epoke med den nye
policyen er nå også fullført: 228 oppdateringer ga 392/392 identiske tensorer.
Sammenligningen gjelder samme RTX 5090 og programvareoppsett.

## Ny kjøring: en hel epoke gjentatt eksakt

Den nye kjøringen brukte samme 16 bøker og grunnmodell, 3 647 treningsvinduer
og 1 867 264 neste-token-mål. Begge LoRA-faktorene A og B ble trent. Oppskrift
versjon 3 fastsetter én epoke, inkludert en læringsrateplan over denne epoken.
Trening og evaluering tok 26,66 minutter; full replay tok 19,82 minutter.
En separat CPU-prosess kontrollerte replay-kandidatens 392 tensorer på nytt,
gjenskapte alle 4 427 tokenvinduer og verifiserte 310/310 eksporterte tensorer.
Seks signerte historikkposter og en egen release-signatur binder kjeden.
Alle 39 filer i release-manifestet ble kontrollert mot sine hasher.

Den bevarte replay-kandidaten har identiske tensorverdier. Filhashene er ulike
fordi lagringsformatets metadata er ulike; tensorlikhet og identiske filbyte er
to forskjellige kontroller. Begge filhashene er registrert. Ingen epsilon ble
brukt for å godkjenne resultatet.

På det fryste testsettet falt økonomi-NLL fra 2,998517 til 2,517980 og
litteratur-NLL fra 3,713179 til 3,303516. De små kontrollsettene gikk fra 8/12
til 9/12 økonomisvar og fra 4/6 til 5/6 generelle svar. Dette er beskrivende
resultater, ikke en bred måling av modellens evner.

Signaturer og replikasjon viser hvilke byte og beregninger som er dokumentert.
De etablerer ikke uavhengige organisasjoner, årsakssammenhengen mellom en
bestemt boksetning og et svar, eller komplett historikk for grunnmodellens trening.
Qwen-kjøringen har lokale signaturer, ikke et Oracle-sertifikat.

Eldre modellnøkkel: `3eedb69bceac39ca67320c3a6d704fe12dc4462cac7a56171ab804f2bc3ec537`.
Signert slutthode: `9e43d4e9ac8be1467131f457d947881c2286b005792f5501cb4d70b6c7d46e64`.
GGUF SHA256 for valgt modell: `5c0030da683551e25f3f1eb5fc02312ca3246b3e5ae8dd44bd0b2605a26fd173`.

## Arkitektur og distribuert tilbakepropagering

```mermaid
flowchart TB
    S[Kilder og kildehash] --> C[Cortex: originaltekst og Frags]
    C --> D[Tokenizer og Blueprint: data, profil og rekkefølge]
    D --> T[Trenere: lokal forward, backward og optimizer]
    T --> U[Signerte adapterbidrag og innholdsadresserte filer]
    U --> V[Verifier Daemons: repetisjon og reduksjon]
    O[Oracle: CometBFT, opptak, leases og sertifiserte sjekkpunkter] --> T
    V --> O
    O --> M[Modellkonsument: grunnmodell og adapter]
    C <--> P[Collective: signert XOR-DHT, inviterte noder]
    T <--> P
    O --> G[Lokal publiseringsport: quorumkontroll, alder og monoton høyde]
    G --> A[Azure: offentlig leseflate, siste godkjente visningsdata]
    F1[Fremtid: uavhengige operatører, identitet, transport og eclipse-motstand] -.-> P
    F2[Fremtid: optimizer-state, DiLoCo, tensor-/pipeline-parallellisme] -.-> T
    F3[Fremtid: kalibrerte numeriske toleranser og tilfeldige intervallauditer] -.-> V
```

**Implementert i SmolLM2-testnettet:** fire separate CometBFT-validatorer,
midlertidige treningsleases, to vellykkede trenere, to GPU-verifikatorer og
replikerte vektsjekkpunkter. Fire runder ga åtte bidrag og 16/16 eksakte
repetisjoner. En utløpt lease ble overtatt av en ny trener; sen innsending fra
den gamle eieren ble avvist. En kald konsument hentet og kjørte modellen fra én
gjenværende verifikator. Alle nodene i dette forsøket brukte én fysisk vert og GPU.

Modellen er `W = W0 + (alpha/r) BA`. I dette distribuerte forsøket er A fast og
bare B trenes med lokal SGD. Etter lokale steg beregnes
`B_next = FP32(sum_k (n_k / sum_j n_j) * FP64(B_k))` i fast rekkefølge.
Dette er reell lokal tilbakepropagering med periodisk adapteraggregering.
Det er ikke kontinuerlig gradient-allreduce eller oppsplitting av én modell
mellom maskiner. Å gjennomsnitte både lærte A og B separat gir normalt ikke
gjennomsnittet av produktene BA; Qwen-kjøringen kan derfor ikke kobles direkte
til denne reduksjonen uten en ny kontrakt.

**Gjenstår:** distribuert Qwen/AdamW, kontrakt for optimizer-tilstand, eventuell
DiLoCo-ytre optimizer, brede maskinvareforsøk, asynkrone foreldrekonflikter,
tilgjengelighetsutfordringer og utskifting av verifikatorer. Homogen eksakt modus
beholdes. Heterogene grener får egne artefakthasher og eksplisitte vurderinger;
SHA256 blir aldri en «tolerant hash». Cosinus alene fanger ikke endret størrelse
på oppdateringen. Et ferdig numerisk akseptkriterium er ikke implementert.

**Nettverkssplitt nå:** Azure brukes som offentlig leseflate. Lokale ressurser
utfører beregningene; publisering krever en kontrollert quorumkvittering.
Ved bortfall beholdes siste godkjente bilde og merkes foreldet. Dette er
visningskontinuitet, ikke konsensusfremdrift uten quorum. Den nyere lokale
treningskjeden og den eldre hybride kjeden har forskjellige genesis-forankringer;
de blir ikke automatisk slått sammen ved gjenoppkobling.

## Deltakerpilot etter Yngvars innspill

Rådsvisning og proveniensvisning er separate komponenter. De seks rådstekstene
er lagret ordrett. Dokumentasjonsbetingelse A viser ingen ekstra dokumentasjon;
B viser en utviklingsbeskrivelse; C viser de samme setningene med dokumentlenker.
Den aktive dokumentasjonen gjelder nå den nye, fullt gjentatte én-epokersmodellen.
Endringen har egen protokollhash; tidligere økter beholder sine fryste visninger.
Én deltaker beholder samme betingelse gjennom tre scenarioer. Rekkefølgen trekkes
tilfeldig, og hver deltaker ser bare én rådvariant per scenario. Blokker på 24
tildelinger balanserer tre betingelser og åtte kombinasjoner av rådvarianter.
Avbrutte økter beholdes i tildelingsoversikten, ikke omfordelt etter resultatet.

Alle får samme nøytrale forklaring om at rådene er skrevet for forsøket og at
modelldokumentasjonen gjelder en reell demonstrasjonsmodell. Det skal ikke stå
at de faste rådene ble generert av denne modellen. Om forsøket trenger en slik
attribusjon, må rådstimuli og design avklares før rekruttering. Den tidligere
tre-modell-sammenligningen beholdes som et separat teknisk forsøk.

For hver oppgave registreres valg, tillit (1-7), egen sikkerhet (1-7), bruk av
rådet (følge/sjekke/avvise), opplevd vanskelighet (1-7), valgfri begrunnelse,
åpning av dokumentasjon og lenker, visnings- og innsendingsdato. Ingen tall er
forhåndsvalgt. Tilfeldig øktkode erstatter navn. SHA256 binder scenario, råd,
dokumentasjon, grensesnitt og protokollversjon. Økter tåler serverstart på nytt;
gjentatt identisk innsending dobbelttelles ikke. Tidsbruk inkluderer pauser.

Forskerdata lagres bare lokalt og kan eksporteres som JSON. Det offentlige
Azure-nettstedet har ingen innsamlingsendepunkter eller deltakerresultater.
Plattform og deltakergrensesnitt har separat navigasjon uten lenker til hverandre.
Forskerforhåndsvisning skriver ingen økter. Fasit og rådvariantetiketter sendes
ikke med deltakerens oppgave. Dette er en lokal pilot, ikke en sikret ekstern
rekrutteringsportal; forsker- og deltakersider bør separeres med autentisering
før reell flerbrukerdrift.

Yngvar følger opp faglig vurdering av de tre scenarioene, om feilene er plausible,
vanskelighetsnivå, endelig design og analyseplan. De enkle regnestykkene er
kontrollert: 3 600 mot 1 400 kroner spart rente under oppgavens forutsetninger;
0,55 mot 0,45 prosent i fondsgebyr; 108 000 mot 122 000 kroner i nominell
utbetaling. Dette er mekaniske kontroller, ikke en erstatning for fagfellevurdering.
Ingen deltakereffekt eller konklusjon om tillit er målt i denne leveransen.

## Kontrolloppskrift for den nye modellen

Den nye én-epokersmodellen har offentlig nøkkel
`a30bc71e898549c4f43320fe30c6c3ad90779c87fa39ed490fb4ef2349ba8eb5`.
Release-manifestets SHA256 er
`fffeadd1ec58342fe2a9cfb894e65f37118e11e79f5c17ee9ea7857ebec99efa`.
Slutthodet i signaturkjeden er
`9dbd3d40e9c67c77789d286734d41a2fa60b7ada1dddeceeba44666d15390f86`.

1. **Hent evidenspakken:** velg full-epoch replay på konsollens evidensside.
   ZIP-pakken inneholder oppskrift, kilderegister, signaturer, kjøretidsversjoner,
   verifikasjonskode og måleresultater. Sammenlign filene med SHA256-manifestet.
2. **Kontroller signaturen:** `release-attestation.json` signerer hashen til
   `release.json`. Bruk nøkkelen ovenfor og kontroller de seks postene i
   `lineage.jsonl`, inkludert rekkefølge og forbindelsen til slutthodet.
3. **Følg inngangene:** `source-lock.json` oppgir kilder og hasher;
   `dataset-lock.json` binder tokenizer, partisjoner og tokenvinduer;
   `recipe.json` fastsetter optimizer, rekkefølge, policy og antall oppdateringer.
4. **Les de separate kontrollene:** `verification.json` gjelder kilder og tokens;
   `replay-verification.json` gjelder treningsgjentakelsen;
   `replay-artifact-verification.json` sammenligner de bevarte kandidatene på CPU;
   `merge-verification.json` etterprøver de ferdige modellvektene.
5. **Gjenta beregningen:** med de hashbundne modell- og datafilene kan en ekstern
   kontrollør kjøre den bevarte replay-koden i det festede CUDA-oppsettet.
   Treningsadapter og replay-kandidat er begge bevart. CPU-verifikasjonskoden
   følger pakken og kan kontrollere de faktiske tensorene og eksporten separat.

Dermed kan kontrolløren gå fra en kildeadresse til eksakte datafiler, videre til
treningsoppskriften og til de ferdige vektene. Signaturen gir en etterprøvbar
forbindelse mellom disse artefaktene; replay gir den målte beregningskontrollen.

## Filer for teknisk oppfølging

- `docs/results/finetune-economics-001/`: metode, signaturer, modellmålinger og opprinnelig replay-feil.
- `docs/results/finetune-repeatability-001/`: kontrollerte tester av bakoverpassering og ny eksakt policy.
- `docs/results/finetune-strict-002/`: full ny epoke, eksakt replay, signert release og bevarte kontrollkandidater.
- `docs/results/oracle-training-run-003/`: sertifisert treningshistorikk, leases og replikkgjenoppretting.
- `docs/training_verification.md`: verifikasjonsdesign og forskningsreferanser.
- `research/trust-study-v2.json`: stimuli og foreløpig forskerrubrikk.
- `docs/trust-study-v2.md`: protokoll, dataordbok og operatørveiledning.

Delbar konsoll og evidens: https://dawlh1013185918state.z1.web.core.windows.net/

Kildekode: https://github.com/drcircuit/DAWL (krever tilgang til repositoriet).

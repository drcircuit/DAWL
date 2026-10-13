// DAWL's ABCI adapter and fixed-validator public checkpoint verifier.
// CometBFT owns proposals, locks, votes, WAL recovery and block synchronization.
package main

import (
 "bytes"
 "context"
 "crypto/sha256"
 "encoding/hex"
 "encoding/json"
 "flag"
 "fmt"
 "io"
 "net/http"
 "os"
 "os/signal"
 "syscall"
 "time"

 server "github.com/cometbft/cometbft/abci/server"
 abci "github.com/cometbft/cometbft/abci/types"
 rpcclient "github.com/cometbft/cometbft/rpc/client"
 rpchttp "github.com/cometbft/cometbft/rpc/client/http"
 "github.com/cometbft/cometbft/types"
)

type bridge struct { abci.BaseApplication; endpoint string; client *http.Client }
type boundedTransport struct { inner http.RoundTripper }
type boundedBody struct { io.Reader; io.Closer }
func (t boundedTransport) RoundTrip(r *http.Request) (*http.Response,error) {
 res,err:=t.inner.RoundTrip(r)
 if err==nil { res.Body=boundedBody{io.LimitReader(res.Body,24<<20),res.Body} }
 return res,err
}
func (b *bridge) call(ctx context.Context, path string, in, out any) error {
 data, err := json.Marshal(in); if err != nil { return err }
 req, err := http.NewRequestWithContext(ctx, "POST", b.endpoint+path, bytes.NewReader(data)); if err != nil { return err }
 req.Header.Set("Content-Type", "application/json")
 res, err := b.client.Do(req); if err != nil { return err }; defer res.Body.Close()
 if res.StatusCode != 200 { return fmt.Errorf("local ABCI state machine returned %d", res.StatusCode) }
 return json.NewDecoder(io.LimitReader(res.Body, 32<<20)).Decode(out)
}
func (b *bridge) Info(ctx context.Context, r *abci.RequestInfo) (*abci.ResponseInfo, error) {
 var out struct { Height int64 `json:"height"`; Hash []byte `json:"hash"` }
 err := b.call(ctx, "/info", r, &out)
 return &abci.ResponseInfo{Data:"DAWL cluster", Version:"1", AppVersion:1, LastBlockHeight:out.Height, LastBlockAppHash:out.Hash}, err
}
func (b *bridge) InitChain(ctx context.Context, r *abci.RequestInitChain) (*abci.ResponseInitChain, error) {
 var out struct { Hash []byte `json:"hash"` }
 err := b.call(ctx, "/init", map[string]any{"chain_id":r.ChainId, "app_state":r.AppStateBytes}, &out)
 return &abci.ResponseInitChain{AppHash:out.Hash}, err
}
func (b *bridge) CheckTx(ctx context.Context, r *abci.RequestCheckTx) (*abci.ResponseCheckTx, error) {
 var out struct { Code uint32 `json:"code"`; Log string `json:"log"` }
 err := b.call(ctx, "/check", map[string]any{"tx":r.Tx}, &out)
 return &abci.ResponseCheckTx{Code:out.Code, Log:out.Log, GasWanted:1}, err
}
func (b *bridge) FinalizeBlock(ctx context.Context, r *abci.RequestFinalizeBlock) (*abci.ResponseFinalizeBlock, error) {
 var out struct { Hash []byte `json:"hash"`; Results []*abci.ExecTxResult `json:"results"` }
 err := b.call(ctx, "/finalize", map[string]any{"height":r.Height, "txs":r.Txs, "misbehavior":r.Misbehavior}, &out)
 return &abci.ResponseFinalizeBlock{AppHash:out.Hash, TxResults:out.Results}, err
}
func (b *bridge) Commit(ctx context.Context, r *abci.RequestCommit) (*abci.ResponseCommit, error) {
 var out map[string]any
 err := b.call(ctx, "/commit", r, &out)
 return &abci.ResponseCommit{}, err
}
func (b *bridge) Query(ctx context.Context, r *abci.RequestQuery) (*abci.ResponseQuery, error) {
 var out struct { Code uint32 `json:"code"`; Height int64 `json:"height"`; Value []byte `json:"value"` }
 err := b.call(ctx, "/query", map[string]any{"path":r.Path, "data":r.Data, "height":r.Height, "prove":r.Prove}, &out)
 return &abci.ResponseQuery{Code:out.Code, Height:out.Height, Value:out.Value}, err
}

func verify(args []string) error {
 flags := flag.NewFlagSet("verify", flag.ContinueOnError)
 endpoint := flags.String("rpc", "http://127.0.0.1:26657", "reachable consensus RPC")
 genesis := flags.String("genesis", "", "independently obtained genesis file")
 pin := flags.String("genesis-sha256", "", "independent genesis SHA256 pin")
 minHeight := flags.Int64("min-height",0,"minimum previously verified state height")
 maxAge := flags.Duration("max-age",0,"maximum certificate age; zero disables freshness policy")
 if err := flags.Parse(args); err != nil { return err }
 data, err := os.ReadFile(*genesis); if err != nil { return err }
 digest := sha256.Sum256(data)
 if len(*pin)!=64 || hex.EncodeToString(digest[:]) != *pin { return fmt.Errorf("genesis pin mismatch") }
 gen, err := types.GenesisDocFromJSON(data); if err != nil { return err }
 validators := make([]*types.Validator, len(gen.Validators))
 for i,v := range gen.Validators { validators[i] = types.NewValidator(v.PubKey, v.Power) }
 set := types.NewValidatorSet(validators)
 transport:=http.DefaultTransport.(*http.Transport).Clone(); transport.Proxy=nil
 httpClient:=&http.Client{Timeout:10*time.Second,Transport:boundedTransport{transport},
  CheckRedirect:func(*http.Request,[]*http.Request)error{return fmt.Errorf("RPC redirects are not accepted")}}
 client, err := rpchttp.NewWithClient(*endpoint, "/websocket",httpClient); if err != nil { return err }
 ctx,cancel := context.WithTimeout(context.Background(),30*time.Second); defer cancel()
 q,err := client.ABCIQueryWithOptions(ctx,"/state",nil,rpcclient.ABCIQueryOptions{}); if err!=nil { return err }
 if q.Response.Code!=0 || q.Response.Height<1 { return fmt.Errorf("no committed application state") }
 if q.Response.Height<*minHeight { return fmt.Errorf("certified height rollback") }
 h := q.Response.Height+1
 var signed *types.SignedHeader
 for i:=0;i<30;i++ {
  c,e:=client.Commit(ctx,&h)
  if e==nil { signed=&c.SignedHeader; break }; time.Sleep(500*time.Millisecond)
 }
 if signed==nil { return fmt.Errorf("next-height app-hash certificate unavailable") }
 if err:=signed.ValidateBasic(gen.ChainID); err!=nil { return err }
 if !bytes.Equal(signed.Header.ValidatorsHash,set.Hash()) || !bytes.Equal(signed.Header.NextValidatorsHash,set.Hash()) {
  return fmt.Errorf("validator set differs from fixed genesis; dynamic sets are unsupported")
 }
 if err:=set.VerifyCommitLight(gen.ChainID,signed.Commit.BlockID,h,signed.Commit); err!=nil { return err }
 if *maxAge<0 { return fmt.Errorf("invalid certificate age policy") }
 if *maxAge>0 {
  now:=time.Now()
  if signed.Header.Time.After(now.Add(10*time.Second)) || now.Sub(signed.Header.Time)>*maxAge {
   return fmt.Errorf("certificate outside configured clock/freshness window")
  }
 }
 appHash:=sha256.Sum256(q.Response.Value)
 if !bytes.Equal(appHash[:],signed.Header.AppHash) { return fmt.Errorf("state bytes differ from certified app hash") }
 var state json.RawMessage = q.Response.Value
 return json.NewEncoder(os.Stdout).Encode(map[string]any{"verified":true,"chain_id":gen.ChainID,
  "state_height":q.Response.Height,"certificate_height":h,"app_hash":hex.EncodeToString(appHash[:]),
  "genesis_sha256":*pin,"state":state,"signed_header":signed,
  "scope":"Fixed-genesis voting-power quorum certifies state bytes; it does not prove worker computation or freshness."})
}

func run() error {
 if len(os.Args)>1 && os.Args[1]=="verify" { return verify(os.Args[2:]) }
 flags:=flag.NewFlagSet("bridge",flag.ContinueOnError)
 address:=flags.String("listen","tcp://127.0.0.1:26658","local ABCI socket")
 endpoint:=flags.String("application","http://127.0.0.1:26659","local deterministic application")
 if err:=flags.Parse(os.Args[1:]);err!=nil{return err}
 b:=&bridge{endpoint:*endpoint,client:&http.Client{Timeout:30*time.Second}}
 s,err:=server.NewServer(*address,"socket",b); if err!=nil{return err}
 if err=s.Start();err!=nil{return err}
 stop:=make(chan os.Signal,1); signal.Notify(stop,syscall.SIGINT,syscall.SIGTERM); <-stop
 return s.Stop()
}
func main() { if err:=run();err!=nil { fmt.Fprintln(os.Stderr,err);os.Exit(1) } }

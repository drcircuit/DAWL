// Offline verification of exported fixed-genesis DAWL certificates.
package main

import (
    "bytes"
    "crypto/sha256"
    "encoding/hex"
    "encoding/json"
    "flag"
    "fmt"
    "os"
    "github.com/cometbft/cometbft/types"
)

func read(path string, limit int64) ([]byte, error) {
    info, err := os.Stat(path)
    if err != nil { return nil, err }
    if info.Size() > limit { return nil, fmt.Errorf("evidence exceeds bound") }
    return os.ReadFile(path)
}

func run() error {
    genesisPath := flag.String("genesis", "", "public genesis file")
    pin := flag.String("genesis-sha256", "", "independently obtained genesis hash")
    certificatePath := flag.String("certificate", "", "canonical exported certificate JSON")
    flag.Parse()
    genesis, err := read(*genesisPath, 1024*1024); if err != nil { return err }
    digest := sha256.Sum256(genesis)
    if len(*pin)!=64 || hex.EncodeToString(digest[:])!=*pin { return fmt.Errorf("genesis pin mismatch") }
    gen, err := types.GenesisDocFromJSON(genesis); if err != nil { return err }
    data, err := read(*certificatePath, 24*1024*1024); if err != nil { return err }
    var certificate struct {
        Genesis string `json:"genesis_sha256"`
        State json.RawMessage `json:"state"`
        Header types.SignedHeader `json:"signed_header"`
    }
    if err=json.Unmarshal(data,&certificate); err!=nil { return err }
    if certificate.Genesis!=*pin { return fmt.Errorf("certificate belongs to another genesis") }
    signed:=certificate.Header
    if err=signed.ValidateBasic(gen.ChainID); err!=nil { return err }
    validators:=make([]*types.Validator,len(gen.Validators))
    for i,v:=range gen.Validators { validators[i]=types.NewValidator(v.PubKey,v.Power) }
    set:=types.NewValidatorSet(validators)
    if !bytes.Equal(signed.Header.ValidatorsHash,set.Hash()) || !bytes.Equal(signed.Header.NextValidatorsHash,set.Hash()) {
        return fmt.Errorf("fixed validator set mismatch")
    }
    if err=set.VerifyCommitLight(gen.ChainID,signed.Commit.BlockID,signed.Header.Height,signed.Commit); err!=nil { return err }
    var state struct { Height int64 `json:"height"`; Chain string `json:"chain_id"` }
    if err=json.Unmarshal(certificate.State,&state); err!=nil { return err }
    stateHash:=sha256.Sum256(certificate.State)
    if state.Height+1!=signed.Header.Height || state.Chain!=gen.ChainID || !bytes.Equal(stateHash[:],signed.Header.AppHash) {
        return fmt.Errorf("state bytes or height differ from signed header")
    }
    return json.NewEncoder(os.Stdout).Encode(map[string]any{"verified":true,"state_height":state.Height,
        "chain_id":state.Chain,"app_hash":hex.EncodeToString(stateHash[:]),
        "scope":"Historical fixed-genesis quorum and exact canonical state bytes; no present-time freshness or computation proof."})
}

func main() { if err:=run(); err!=nil { fmt.Fprintln(os.Stderr,err); os.Exit(1) } }

// Package plugins — ut-translations' till-verifier harness (ut-docs#4367).
//
// This file is NOT built in ut-translations (it has no go.mod). tools/
// till_verify.py copies it into a throwaway copy of universal-till at the
// bundle's source_commit, as internal/plugins/zz_utt_bundle_harness_test.go,
// and runs `go test -run '^TestUTTBundle'`. Living inside package plugins is
// what lets it call the till's real install path (MarketplaceInstaller.
// installBundleFile → VerifyArtifact, extract, VerifyManifest, the signature
// and checksum match, compatibility) and the real locale loader
// (Manager.syncLocales via Init + SetLocalizer). Go's internal/ rule forbids
// importing these from another module, so a copy-in is the only way to run
// the real code rather than a re-implementation of it.
//
// It only uses the package's exported/unexported production API and its own
// utt-prefixed helpers, never universal-till's test helpers, so a rename
// there cannot silently change what this checks; a rename of the production
// API fails compilation, which fails the run (never a pass).
//
// Input (env): UTT_BUNDLE = the unsigned bundle tar.gz build_bundle.py made,
// UTT_LOCALE = its locale code.
package plugins

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/universaltill/universal-till/internal/buildinfo"
	"github.com/universaltill/universal-till/internal/config"
	appdb "github.com/universaltill/universal-till/internal/db"
	"github.com/universaltill/universal-till/internal/paths"
)

type uttEntry struct {
	hdr  *tar.Header
	data []byte
}

func uttReadTarGz(t *testing.T, raw []byte) []uttEntry {
	t.Helper()
	gz, err := gzip.NewReader(bytes.NewReader(raw))
	if err != nil {
		t.Fatalf("bundle is not gzip: %v", err)
	}
	defer gz.Close()
	tr := tar.NewReader(gz)
	var out []uttEntry
	for {
		hdr, err := tr.Next()
		if err == io.EOF {
			return out
		}
		if err != nil {
			t.Fatalf("read bundle: %v", err)
		}
		data, err := io.ReadAll(tr)
		if err != nil {
			t.Fatalf("read %s: %v", hdr.Name, err)
		}
		out = append(out, uttEntry{hdr: hdr, data: data})
	}
}

func uttWriteTarGz(t *testing.T, entries []uttEntry) []byte {
	t.Helper()
	var buf bytes.Buffer
	gw := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gw)
	for _, e := range entries {
		h := *e.hdr
		h.Size = int64(len(e.data))
		if err := tw.WriteHeader(&h); err != nil {
			t.Fatalf("write header: %v", err)
		}
		if _, err := tw.Write(e.data); err != nil {
			t.Fatalf("write entry: %v", err)
		}
	}
	if err := tw.Close(); err != nil {
		t.Fatalf("close tar: %v", err)
	}
	if err := gw.Close(); err != nil {
		t.Fatalf("close gzip: %v", err)
	}
	return buf.Bytes()
}

// uttReplace returns a copy of the bundle with one entry's bytes changed.
func uttReplace(t *testing.T, raw []byte, name string, edit func([]byte) []byte) []byte {
	t.Helper()
	entries := uttReadTarGz(t, raw)
	found := false
	for i := range entries {
		if strings.TrimPrefix(entries[i].hdr.Name, "./") == name {
			entries[i].data = edit(entries[i].data)
			found = true
		}
	}
	if !found {
		t.Fatalf("bundle has no %s", name)
	}
	return uttWriteTarGz(t, entries)
}

func uttSum(b []byte) string {
	s := sha256.Sum256(b)
	return hex.EncodeToString(s[:])
}

// uttSignBundle does what the marketplace's approve step does
// (ut-cloud internal/signing.SignBundle): sign the canonical manifest —
// json.Marshal of the manifest struct with an empty signature, the form
// VerifyManifest checks (pinned against ut-cloud by
// marketplace_signature_crossrepo_test.go) — and write it back into the
// archive. Returns the signed bundle, the signature and its sha256.
func uttSignBundle(t *testing.T, raw []byte, priv ed25519.PrivateKey) ([]byte, string, string) {
	t.Helper()
	var sig string
	signed := uttReplace(t, raw, "manifest.json", func(b []byte) []byte {
		var m Manifest
		if err := json.Unmarshal(b, &m); err != nil {
			t.Fatalf("manifest.json: %v", err)
		}
		m.Signature = ""
		canonical, err := json.Marshal(m)
		if err != nil {
			t.Fatalf("canonical manifest: %v", err)
		}
		sig = hex.EncodeToString(ed25519.Sign(priv, canonical))
		m.Signature = sig
		out, err := json.Marshal(m)
		if err != nil {
			t.Fatalf("signed manifest: %v", err)
		}
		return out
	})
	return signed, sig, uttSum(signed)
}

type uttLocalizer struct{ layers config.PluginLocales }

func (l *uttLocalizer) SetPluginLocales(pl config.PluginLocales) { l.layers = pl }

type uttTill struct {
	cfg       *config.Config
	installer *MarketplaceInstaller
	dataDir   string
}

// uttNewTill is a fresh till: its own data dir, migrated database and a
// marketplace key the harness signs with.
func uttNewTill(t *testing.T, pub ed25519.PublicKey) *uttTill {
	t.Helper()
	dataDir := t.TempDir()
	prev := paths.DataDir()
	paths.Init(dataDir)
	t.Cleanup(func() { paths.Init(prev) })
	database, err := appdb.Open(filepath.Join(dataDir, "till.db"))
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	t.Cleanup(func() { database.DB.Close() })
	cfg := &config.Config{Env: "test", Marketplace: config.MarketplaceConfig{PublicKey: hex.EncodeToString(pub)}}
	inst, err := NewMarketplaceInstaller(cfg, nil, database.DB)
	if err != nil {
		t.Fatalf("NewMarketplaceInstaller: %v", err)
	}
	if err := os.MkdirAll(inst.downloadTmpDir, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	return &uttTill{cfg: cfg, installer: inst, dataDir: dataDir}
}

func (u *uttTill) install(t *testing.T, bundle []byte, checksum, signature string) error {
	t.Helper()
	p := filepath.Join(u.dataDir, "download.tar.gz")
	if err := os.WriteFile(p, bundle, 0o644); err != nil {
		t.Fatalf("write bundle: %v", err)
	}
	_, err := u.installer.installBundleFile(context.Background(), bundleInstallSpec{
		BundlePath: p,
		ListingID:  "utt-listing",
		Checksum:   checksum,
		Signature:  signature,
		SourceURL:  "https://marketplace.invalid/utt",
		TrustTier:  "verified",
		DeviceArch: "linux/amd64",
	})
	return err
}

func uttEntryBytes(t *testing.T, raw []byte, name string) []byte {
	t.Helper()
	for _, e := range uttReadTarGz(t, raw) {
		if strings.TrimPrefix(e.hdr.Name, "./") == name {
			return e.data
		}
	}
	t.Fatalf("bundle has no %s", name)
	return nil
}

func TestUTTBundleTillVerifier(t *testing.T) {
	bundlePath, locale := os.Getenv("UTT_BUNDLE"), os.Getenv("UTT_LOCALE")
	if bundlePath == "" || locale == "" {
		t.Fatal("UTT_BUNDLE and UTT_LOCALE must be set (run through ut-translations tools/till_verify.py)")
	}
	raw, err := os.ReadFile(bundlePath)
	if err != nil {
		t.Fatalf("read bundle: %v", err)
	}
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatalf("keygen: %v", err)
	}
	signed, sig, sum := uttSignBundle(t, raw, priv)
	localeFile := "locales/" + locale + ".json"

	var info struct {
		GeneratedAt string `json:"generated_at"`
	}
	if err := json.Unmarshal(uttEntryBytes(t, raw, "bundle.json"), &info); err != nil {
		t.Fatalf("bundle.json: %v", err)
	}
	generated, err := time.Parse(time.RFC3339, info.GeneratedAt)
	if err != nil {
		t.Fatalf("bundle.json generated_at %q is not RFC 3339: %v", info.GeneratedAt, err)
	}

	t.Run("signed bundle installs and loads as a fresh first-party layer", func(t *testing.T) {
		till := uttNewTill(t, pub)
		if err := till.install(t, signed, sum, sig); err != nil {
			t.Fatalf("the till refused the untampered signed bundle: %v", err)
		}
		prevBuild := buildinfo.BuildDate
		buildinfo.BuildDate = generated.Add(-time.Hour).UTC().Format(time.RFC3339)
		t.Cleanup(func() { buildinfo.BuildDate = prevBuild })

		var want map[string]string
		if err := json.Unmarshal(uttEntryBytes(t, raw, localeFile), &want); err != nil {
			t.Fatalf("%s: %v", localeFile, err)
		}
		db := till.installer.db
		m, err := Init(context.Background(), till.cfg, db)
		if err != nil {
			t.Fatalf("Init: %v", err)
		}
		loc := &uttLocalizer{}
		m.SetLocalizer(loc)
		got := loc.layers.FirstPartyFresh[locale]
		if len(want) == 0 {
			if len(got) != 0 {
				t.Fatalf("empty bundle loaded strings: %v", got)
			}
		} else if !reflect.DeepEqual(got, want) {
			t.Fatalf("FirstPartyFresh[%s] = %d strings, want the bundle's %d exactly (stale=%d third-party=%d)",
				locale, len(got), len(want), len(loc.layers.FirstPartyStale[locale]), len(loc.layers.ThirdParty[locale]))
		}
		if len(loc.layers.ThirdParty[locale]) != 0 || len(loc.layers.FirstPartyStale[locale]) != 0 {
			t.Fatalf("bundle leaked into another layer: stale=%v third=%v", loc.layers.FirstPartyStale[locale], loc.layers.ThirdParty[locale])
		}
	})

	t.Run("unsigned bundle is refused", func(t *testing.T) {
		till := uttNewTill(t, pub)
		err := till.install(t, raw, uttSum(raw), "")
		if err == nil {
			t.Fatal("the till installed the unsigned bundle")
		}
		t.Logf("refused: %v", err)
	})

	tamper := map[string]func([]byte) []byte{
		localeFile: func(b []byte) []byte {
			var m map[string]string
			if err := json.Unmarshal(b, &m); err != nil {
				t.Fatalf("%s: %v", localeFile, err)
			}
			if m == nil {
				m = map[string]string{}
			}
			m["utt.tampered"] = "GoBD-konform"
			out, _ := json.Marshal(m)
			return out
		},
		"bundle.json": func(b []byte) []byte {
			return bytes.Replace(b, []byte(info.GeneratedAt), []byte("2099-01-01T00:00:00Z"), 1)
		},
	}
	for name, edit := range tamper {
		t.Run("tampered "+name+" is refused", func(t *testing.T) {
			till := uttNewTill(t, pub)
			// The marketplace's download token carries the checksum of the
			// bundle it signed; the bytes on the wire were changed.
			err := till.install(t, uttReplace(t, signed, name, edit), sum, sig)
			if err == nil || !strings.Contains(err.Error(), "checksum") {
				t.Fatalf("want a checksum refusal, got %v", err)
			}
		})
	}

	t.Run("tampered manifest is refused even with a matching checksum", func(t *testing.T) {
		till := uttNewTill(t, pub)
		forged := uttReplace(t, signed, "manifest.json", func(b []byte) []byte {
			return bytes.Replace(b, []byte(`"min_pos_version":"`), []byte(`"min_pos_version":"0.`), 1)
		})
		err := till.install(t, forged, uttSum(forged), sig)
		if err == nil || !strings.Contains(err.Error(), "signature") {
			t.Fatalf("want a signature refusal, got %v", err)
		}
	})

	t.Run("signature from another key is refused", func(t *testing.T) {
		otherPub, _, err := ed25519.GenerateKey(rand.Reader)
		if err != nil {
			t.Fatal(err)
		}
		till := uttNewTill(t, otherPub)
		err = till.install(t, signed, sum, sig)
		if err == nil || !strings.Contains(err.Error(), "signature") {
			t.Fatalf("want a signature refusal, got %v", err)
		}
	})
}

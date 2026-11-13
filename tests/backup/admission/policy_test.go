package admission

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"

	"cel.dev/cel-go/cel"
	"cel.dev/cel-go/common/types"
	"go.yaml.in/yaml/v3"
)

type typedSpec struct {
	Suspend bool `cel:"suspend"`
}

type typedObject struct {
	Spec     typedSpec      `cel:"spec"`
	Metadata map[string]any `cel:"metadata"`
}

func TestExactSuspendPolicy(t *testing.T) {
	content, err := os.ReadFile("../../../kubernetes/backup/admission.yaml")
	if err != nil {
		t.Fatal(err)
	}
	var policy struct {
		Spec struct {
			MatchConditions []struct {
				Expression string `yaml:"expression"`
			} `yaml:"matchConditions"`
			Validations []struct {
				Expression string `yaml:"expression"`
			} `yaml:"validations"`
		} `yaml:"spec"`
	}
	if err := yaml.Unmarshal(content, &policy); err != nil {
		t.Fatal(err)
	}
	env, err := cel.NewEnv(cel.Variable("object", cel.DynType), cel.Variable("oldObject", cel.DynType), cel.Variable("request", cel.DynType))
	if err != nil {
		t.Fatal(err)
	}
	provider, _, err := types.ComposeTypes(env.CELTypeProvider(), env.CELTypeAdapter(),
		reflect.TypeOf(typedObject{}), types.ParseStructTags(true), types.NativeTypeAlias("admission.typedObject"))
	if err != nil {
		t.Fatal(err)
	}
	typedEnv, err := cel.NewEnv(
		cel.CustomTypeProvider(provider),
		cel.Variable("object", cel.ObjectType("admission.typedObject")),
		cel.Variable("oldObject", cel.ObjectType("admission.typedObject")),
	)
	if err != nil {
		t.Fatal(err)
	}
	compile := func(expression string) cel.Program {
		ast, issues := env.Compile(expression)
		if issues.Err() != nil {
			t.Fatal(issues.Err())
		}
		program, err := env.Program(ast)
		if err != nil {
			t.Fatal(err)
		}
		return program
	}
	var programs []cel.Program
	for _, validation := range policy.Spec.Validations {
		if _, issues := typedEnv.Compile(validation.Expression); issues.Err() != nil {
			t.Fatalf("CRD object type check: %v", issues.Err())
		}
		programs = append(programs, compile(validation.Expression))
	}
	if len(programs) != 2 {
		t.Fatal("expected both safety predicates")
	}
	original := map[string]any{
		"metadata": map[string]any{"name": "apps", "namespace": "flux-system", "annotations": map[string]any{"owner": "operator"}},
		"spec":     map[string]any{"sourceRef": map[string]any{"kind": "GitRepository", "name": "homelab"}, "path": "./kubernetes/apps", "prune": true},
	}
	clone := func() map[string]any {
		data, _ := json.Marshal(original)
		var obj map[string]any
		if err := json.Unmarshal(data, &obj); err != nil {
			t.Fatal(err)
		}
		return obj
	}
	cases := []struct {
		name    string
		mutate  func(map[string]any)
		allowed bool
	}{
		{"suspend", func(o map[string]any) { o["spec"].(map[string]any)["suspend"] = true }, true},
		{"resume", func(o map[string]any) { o["spec"].(map[string]any)["suspend"] = false }, true},
		{"source escalation", func(o map[string]any) {
			o["spec"].(map[string]any)["sourceRef"] = map[string]any{"kind": "GitRepository", "name": "attacker"}
		}, false},
		{"path escalation", func(o map[string]any) { o["spec"].(map[string]any)["path"] = "./attacker" }, false},
		{"remove safeguard", func(o map[string]any) { delete(o["spec"].(map[string]any), "prune") }, false},
		{"new spec field", func(o map[string]any) { o["spec"].(map[string]any)["serviceAccountName"] = "admin" }, false},
		{"metadata mutation", func(o map[string]any) {
			o["metadata"].(map[string]any)["annotations"] = map[string]any{"owner": "attacker"}
		}, false},
		{"add owner", func(o map[string]any) {
			o["metadata"].(map[string]any)["ownerReferences"] = []any{map[string]any{"uid": "attacker"}}
		}, false},
		{"nonboolean suspend", func(o map[string]any) { o["spec"].(map[string]any)["suspend"] = "true" }, false},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			object := clone()
			object["spec"].(map[string]any)["suspend"] = true
			test.mutate(object)
			allowed := true
			for _, program := range programs {
				value, _, err := program.Eval(map[string]any{"object": object, "oldObject": original})
				if err != nil || value.Value() != true {
					allowed = false
				}
			}
			if allowed != test.allowed {
				t.Fatalf("allowed=%v, want %v", allowed, test.allowed)
			}
		})
	}
	match := compile(policy.Spec.MatchConditions[0].Expression)
	for _, username := range []string{"system:serviceaccount:homelab:backup-runner", "system:serviceaccount:homelab:backup-watchdog", "system:serviceaccount:flux-system:kustomize-controller"} {
		value, _, err := match.Eval(map[string]any{"request": map[string]any{"userInfo": map[string]any{"username": username}}})
		if err != nil {
			t.Fatal(err)
		}
		expected := username != "system:serviceaccount:flux-system:kustomize-controller"
		if value.Value() != expected {
			t.Fatalf("wrong identity scope for %s", username)
		}
	}
}

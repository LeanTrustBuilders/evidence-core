import TrustAnnotations

/-!
# Fixture for the extractor

Version A. `test/fixture-b` overlays some of these files with a version B, and `test/check.py`
checks how every hash of the declaration key moves between the two.
-/

namespace Fixture

/-- A definition that version B changes. -/
def double (n : Nat) : Nat := 2 * n

/-- A definition that version B leaves alone. -/
def triple (n : Nat) : Nat := n + n + n

/-- Its statement mentions `double`, which B changes: stale underneath. -/
theorem double_zero : double 0 = 0 := by simp [double]

/-- Its statement changes in B: stale. -/
theorem triple_one : triple 1 = 1 + 1 + 1 := rfl

/-- Only its proof changes in B: current. -/
theorem triple_two : triple 2 = 6 := by unfold triple; rfl

/-- Only a binder name changes in B: current. -/
theorem triple_comm (b : Nat) : triple b = b + b + b := rfl

/-- Renamed in B to `triple_three'`: carried over by meaning hash. -/
theorem triple_three' : triple 3 = 9 := rfl

/-- A statement written with `lemma`-style intent, keyword `theorem` here. -/
@[claim "Fixture, Theorem 1"]
theorem triple_pos (n : Nat) (h : 0 < n) : 0 < triple n := by
  unfold triple; omega

def IsSmall (n : Nat) : Prop := n < 10

@[example_of IsSmall] theorem isSmall_three : IsSmall 3 := by unfold IsSmall; omega
@[nonexample_of IsSmall] theorem not_isSmall_twelve : ¬ IsSmall 12 := by unfold IsSmall; omega

/-- A structure, with a field that is a proof. -/
structure Pos where
  val : Nat
  pos : 0 < val

/-- A project lemma used only inside a proof field. -/
theorem one_pos' : 0 < 1 := Nat.one_pos

/-- A definition whose value carries a proof: its `meaning` edges skip the lemma the proof uses,
its `term` edges do not. -/
def one : Pos := ⟨1, one_pos'⟩

instance : Inhabited Pos := ⟨one⟩

end Fixture

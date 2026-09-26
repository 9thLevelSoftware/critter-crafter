using System;
using System.Collections.Generic;
using CritterCrafter.Locomotion;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.AI;

namespace CritterCrafter.Tests
{
    /// <summary>
    /// In-repo stand-in for the game's threat factory. Adds NavMeshAgent here so
    /// CritterCrafter.Runtime can stay AI-free. Not a game-named type.
    /// </summary>
    public sealed class FakeThreatFactory : ICreatureVisualFactory
    {
        readonly CritterLibrary _library;
        readonly AssemblyOptions _options;
        readonly LocomotionData _locomotion;

        public FakeThreatFactory(CritterLibrary library, AssemblyOptions options, LocomotionData locomotion)
        {
            _library = library;
            _options = options;
            _locomotion = locomotion;
        }

        /// <summary>Options last passed into Assemble (collision plumbing, not a collider).</summary>
        public AssemblyOptions LastAssembleOptions { get; private set; }

        public GameObject Build(CreatureSpawnRequest request)
        {
            var opts = _options;
            LastAssembleOptions = opts;
            var creature = CreatureAssembler.Assemble(_library, request.recipe, opts);
            if (creature.GetComponent<CreatureGait>() == null)
                creature.gameObject.AddComponent<CreatureGait>();
            var threat = ThreatHierarchy.Wrap(creature, request.archetypeId);
            var agent = threat.AddComponent<NavMeshAgent>();
            agent.acceleration = 999f;
            agent.angularSpeed = 999f;
            agent.updatePosition = true;
            agent.speed = (float)(_locomotion != null ? _locomotion.v_run_mps : 2.5);
            return threat;
        }
    }

    public class M5AdapterTests
    {
        readonly List<GameObject> _objects = new List<GameObject>();
        CritterLibrary _library;

        [SetUp]
        public void SetUp() => _library = StubLibrary();

        [TearDown]
        public void Cleanup()
        {
            foreach (var go in _objects) if (go != null) UnityEngine.Object.DestroyImmediate(go);
            _objects.Clear();
            if (_library != null) UnityEngine.Object.DestroyImmediate(_library);
            _library = null;
        }

        static CritterLibrary StubLibrary()
        {
            var catalog = new CatalogData
            {
                schema_version = RecipeGenerator.SchemaVersion,
                document_kind = "critter_library",
                library_id = "test_library",
                version = RecipeGenerator.LibraryVersion,
                generator = new GeneratorInfo { algorithm = RecipeGenerator.Algorithm, rng = "splitmix64" },
                limits = new CatalogLimits { max_triangles = 30000, max_bones = 120, max_parts = 16, max_influences = 4 },
                gait_profiles = Array.Empty<GaitProfile>(),
                binding_profiles = Array.Empty<BindingProfile>(),
                branch_templates = Array.Empty<BranchTemplate>(),
                skeletons = Array.Empty<SkeletonData>(),
                parts = Array.Empty<PartData>(),
                pools = Array.Empty<PoolData>(),
            };
            var library = ScriptableObject.CreateInstance<CritterLibrary>();
            library.EditorSetContents(new TextAsset(JsonUtility.ToJson(catalog)),
                Array.Empty<CritterLibrary.SkeletonEntry>(), Array.Empty<CritterLibrary.PartEntry>());
            return library;
        }

        static CreatureSpawnRequest Request(string id, string recipeId = "r1") => new CreatureSpawnRequest
        {
            archetypeId = id,
            recipe = new CritterRecipe { recipe_id = recipeId },
        };

        [Test]
        public void FakeThreatFactoryMatchesThreatHierarchyContract()
        {
            const string id = "scout";
            var loco = new LocomotionData { v_run_mps = 3.75 };
            var options = AssemblyOptions.Default;
            options.collision = CreatureCollision.None;
            var factory = new FakeThreatFactory(_library, options, loco);

            var root = factory.Build(Request(id));
            _objects.Add(root);

            Assert.AreEqual("Threat_" + id, root.name);
            var mesh = root.transform.Find("Mesh");
            Assert.IsNotNull(mesh, "child named Mesh");
            Assert.AreEqual("Mesh", mesh.name);
            Assert.AreEqual(1, mesh.childCount);
            var creature = mesh.GetChild(0).GetComponent<AssembledCreature>();
            Assert.IsNotNull(creature, "assembler output under Mesh");

            var agent = root.GetComponent<NavMeshAgent>();
            Assert.IsNotNull(agent, "NavMeshAgent on Threat root");
            Assert.AreEqual((float)loco.v_run_mps, agent.speed);
            Assert.AreEqual(999f, agent.acceleration);
            Assert.AreEqual(999f, agent.angularSpeed);
            Assert.IsTrue(agent.updatePosition);

            var gait = creature.GetComponent<CreatureGait>();
            Assert.IsNotNull(gait);
            var order = (DefaultExecutionOrder)Attribute.GetCustomAttribute(
                typeof(CreatureGait), typeof(DefaultExecutionOrder));
            Assert.IsNotNull(order);
            Assert.AreEqual(1000, order.order);

            Assert.AreEqual(CreatureCollision.None, factory.LastAssembleOptions.collision);
        }

        [Test]
        public void AssemblyOptionsCollisionIsPlumbedIntoAssemble()
        {
            var loco = new LocomotionData { v_run_mps = 2.5 };
            foreach (var collision in new[] { CreatureCollision.None, CreatureCollision.SingleCapsule })
            {
                var options = AssemblyOptions.Default;
                options.collision = collision;
                var factory = new FakeThreatFactory(_library, options, loco);
                var root = factory.Build(Request("opt"));
                _objects.Add(root);
                Assert.AreEqual(collision, factory.LastAssembleOptions.collision);
                var creature = root.GetComponentInChildren<AssembledCreature>();
                Assert.IsNotNull(creature);
                Assert.That(creature.name, Does.Contain("_fallback"), "Assemble must run with the plumbed options");
            }
        }

        [Test]
        public void NullLocomotionAgentSpeedFallsBackToTwoPointFive()
        {
            var root = new FakeThreatFactory(_library, AssemblyOptions.Default, null)
                .Build(Request("slow"));
            _objects.Add(root);
            Assert.AreEqual(2.5f, root.GetComponent<NavMeshAgent>().speed);
        }

        [Test]
        public void WrapPreservesPoseAndLayerAndDoesNotAddAgent()
        {
            var parent = new GameObject("parent");
            _objects.Add(parent);
            var creatureGo = new GameObject("Creature_pose");
            creatureGo.layer = 8;
            creatureGo.transform.SetParent(parent.transform, false);
            creatureGo.transform.localPosition = new Vector3(1f, 2f, 3f);
            creatureGo.transform.localRotation = Quaternion.Euler(0f, 30f, 0f);
            var creature = creatureGo.AddComponent<AssembledCreature>();

            var threat = ThreatHierarchy.Wrap(creature, "pose");
            _objects.Add(threat);

            Assert.AreEqual(parent.transform, threat.transform.parent);
            Assert.That(Vector3.Distance(threat.transform.localPosition, new Vector3(1f, 2f, 3f)), Is.LessThan(1e-5f));
            Assert.That(Quaternion.Angle(threat.transform.localRotation, Quaternion.Euler(0f, 30f, 0f)), Is.LessThan(0.01f));
            Assert.AreEqual(8, threat.layer);
            var mesh = threat.transform.Find("Mesh");
            Assert.IsNotNull(mesh);
            Assert.AreEqual("Mesh", mesh.name);
            Assert.AreEqual(8, mesh.gameObject.layer);
            Assert.IsNull(threat.GetComponent<NavMeshAgent>());
            Assert.IsNull(creature.GetComponent<NavMeshAgent>());
        }
    }
}

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
        readonly AssemblyOptions _options;
        readonly LocomotionData _locomotion;

        public FakeThreatFactory(AssemblyOptions options, LocomotionData locomotion)
        {
            _options = options;
            _locomotion = locomotion;
        }

        /// <summary>Options last passed into Assemble (collision plumbing, not a collider).</summary>
        public AssemblyOptions LastAssembleOptions { get; private set; }

        public GameObject Build(CreatureSpawnRequest request)
        {
            var opts = _options;
            opts.parent = request.parent;
            opts.layer = request.layer;
            LastAssembleOptions = opts;

            var recipeId = request.recipe != null && !string.IsNullOrEmpty(request.recipe.recipe_id)
                ? request.recipe.recipe_id
                : (request.archetypeId ?? "stub");
            var creatureGo = new GameObject("Creature_" + recipeId);
            if (opts.parent != null) creatureGo.transform.SetParent(opts.parent, false);
            var creature = creatureGo.AddComponent<AssembledCreature>();
            creatureGo.AddComponent<CreatureGait>();

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

        [TearDown]
        public void Cleanup()
        {
            foreach (var go in _objects) if (go != null) UnityEngine.Object.DestroyImmediate(go);
            _objects.Clear();
        }

        [Test]
        public void FakeThreatFactoryMatchesThreatHierarchyContract()
        {
            const string id = "scout";
            var loco = new LocomotionData { v_run_mps = 3.75 };
            var options = AssemblyOptions.Default;
            options.collision = CreatureCollision.None;
            var factory = new FakeThreatFactory(options, loco);
            var request = new CreatureSpawnRequest
            {
                archetypeId = id,
                recipe = new CritterRecipe { recipe_id = "r1" },
            };

            var root = factory.Build(request);
            _objects.Add(root);

            Assert.AreEqual("Threat_" + id, root.name);
            var mesh = root.transform.Find(ThreatHierarchy.MeshChildName);
            Assert.IsNotNull(mesh, "child named Mesh");
            Assert.AreEqual(ThreatHierarchy.MeshChildName, mesh.name);
            Assert.AreEqual(1, mesh.childCount);
            var creature = mesh.GetChild(0).GetComponent<AssembledCreature>();
            Assert.IsNotNull(creature, "assembler output under Mesh");

            var agent = root.GetComponent<NavMeshAgent>();
            Assert.IsNotNull(agent, "NavMeshAgent on Threat root");
            Assert.AreEqual((float)loco.v_run_mps, agent.speed);
            Assert.AreEqual(999f, agent.acceleration);
            Assert.AreEqual(999f, agent.angularSpeed);

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
                var factory = new FakeThreatFactory(options, loco);
                var root = factory.Build(new CreatureSpawnRequest { archetypeId = "opt" });
                _objects.Add(root);
                Assert.AreEqual(collision, factory.LastAssembleOptions.collision);
            }
        }
    }
}
